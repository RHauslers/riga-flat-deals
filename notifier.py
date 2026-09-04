# -*- coding: utf-8 -*-
"""
Daily digest notifier.

Builds an HTML email with:
  - a "vs yesterday" comparison header
  - main deal tables (NEW / PRICE_DROP / REAPPEARED badges)
  - a greyed "Still active from yesterday" section per deal type
  - an unsubscribe footer link
and sends it via SMTP.

SMTP credentials come from environment variables / GitHub secrets:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO,
  UNSUBSCRIBE_URL (optional)

If SMTP is not configured, the HTML is saved to data/digests/digest_YYYY-MM-DD.html
and a notice is printed (pipeline never crashes).
If the recipient has unsubscribed (data/unsubscribed.json), the email is skipped.
"""
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import config
import history
import price_history


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def _fmt_price(v):
    try:
        return f"{float(v):,.0f} EUR".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _fmt_ppu(v):
    try:
        return f"{float(v):,.1f} EUR/m2".replace(",", " ")
    except (TypeError, ValueError):
        return ""


_BADGE_HTML = {
    "NEW": "<span style='color:#27ae60;font-weight:bold'>NEW</span>",
    "PRICE_DROP": "<span style='color:#e67e22;font-weight:bold'>PRICE DROP</span>",
    "REAPPEARED": "<span style='color:#2980b9'>REAPPEARED</span>",
}


def _badge_html(badge, detail):
    b = _BADGE_HTML.get(badge, badge or "")
    if detail:
        b += f" <span style='color:#999;font-size:11px'>({detail})</span>"
    return b


# ---------------------------------------------------------------------------
# table builders
# ---------------------------------------------------------------------------
def _main_row_html(item, price_data=None):
    listing, score, method, badge, detail = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr><td colspan="9" style="padding:2px 5px;'
                        f'border-top:none;background:#fafafa">{timeline_html}</td></tr>')
    return (
        "<tr>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right'>{_fmt_price(listing.get('price_eur'))}</td>"
        f"<td style='text-align:right'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:center'>{score_str}</td>"
        f"<td>{_badge_html(badge, detail)}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')}</a></td>"
        "</tr>"
        f"{timeline_row}"
    )


def _still_row_html(item, price_data=None):
    listing, score, method = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr><td colspan="8" style="padding:2px 5px;'
                        f'border-top:none;background:#fafafa">{timeline_html}</td></tr>')
    return (
        "<tr>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right'>{_fmt_price(listing.get('price_eur'))}</td>"
        f"<td style='text-align:right'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:center'>{score_str}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')}</a></td>"
        "</tr>"
        f"{timeline_row}"
    )


def _table_header(extra_col="Status"):
    return (
        "<table style='border-collapse:collapse;width:100%;font-size:14px'>"
        "<tr style='background:#f0f0f0'>"
        "<th style='text-align:left;padding:4px'>District</th>"
        "<th>Rooms</th><th>m2</th><th>Floor</th>"
        "<th style='text-align:right'>Price</th>"
        "<th style='text-align:right'>EUR/m2</th>"
        "<th>Deal score</th>"
        f"<th>{extra_col}</th>"
        "<th>Source</th></tr>"
    )


def _main_section_html(title, items, subtitle, price_data=None):
    if not items:
        return (f"<h3>{title}</h3>"
                f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
                "<p>No new or changed qualifying deals today.</p>")
    rows = "".join(_main_row_html(it, price_data) for it in items)
    return (
        f"<h3>{title}</h3>"
        f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
        f"{_table_header('Status')}{rows}</table>"
    )


def _still_active_section_html(deal_type, items, price_data=None):
    if not items:
        return ""
    rows = "".join(_still_row_html(it, price_data) for it in items)
    return (
        f"<h3 style='color:#888'>Still active from yesterday — {deal_type}</h3>"
        "<p style='color:#999;font-size:12px'>These deals were in yesterday's "
        "digest and are still among the best today. No action needed unless "
        "you missed them.</p>"
        f"<div style='opacity:0.6'>{_table_header('')}{rows}</table></div>"
    )


# ---------------------------------------------------------------------------
# unsubscribe footer
# ---------------------------------------------------------------------------
def _unsubscribe_html(recipient):
    url = config.UNSUBSCRIBE_URL
    if not url:
        return ("<p class='note'>To unsubscribe, reply to this email with "
                "'unsubscribe' in the subject.</p>")
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}email={quote(recipient or '')}"
    return (f"<p class='note'>To unsubscribe, "
            f"<a href='{full}'>click here</a>.</p>")


# ---------------------------------------------------------------------------
# build the full HTML digest
# ---------------------------------------------------------------------------
def build_html(main_deals, still_active, comparison_html, status_note, recipient="", price_data=None):
    today = date.today().isoformat()
    sections = []

    for dt in config.DEAL_TYPES:
        items = main_deals.get(dt, [])
        subtitle = (f"Top {len(items)} {'new / changed' if items else ''} "
                    f"{dt} deals ranked best-first. Deal score = how much "
                    f"cheaper than the model expects (higher = better deal).")
        sections.append(_main_section_html(dt.upper(), items, subtitle, price_data))

        still = still_active.get(dt, [])
        sa = _still_active_section_html(dt, still, price_data)
        if sa:
            sections.append(sa)

    body_sections = "".join(sections)
    unsub = _unsubscribe_html(recipient)

    # "View in browser" link at top (if site is hosted)
    browser_link = ""
    if config.SITE_URL:
        browser_link = (f'<p style="font-size:13px"><a href="{config.SITE_URL}'
                        '">View in browser</a> &middot; '
                        f'<a href="{config.SITE_URL}/archive.html">Browse past digests</a></p>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;color:#222;max-width:900px;margin:0 auto}}
h2{{color:#1a5276}}h3{{color:#2874a6;border-bottom:2px solid #2874a6;padding-bottom:4px}}
td,th{{border:1px solid #ddd;padding:5px}}a{{color:#2874a6}}
.note{{color:#777;font-size:12px}}</style></head><body>
<h2>Riga flat deals - {today}</h2>
{browser_link}
<p>Districts: {', '.join(config.DISTRICTS.keys())} &middot; Sources: ss.com, city24.lv</p>
<p class="note">Scoring: {status_note}</p>
{comparison_html}
{body_sections}
<hr><p class="note">Generated by Flat_Searcher. Higher deal score = cheaper than
expected for its size/floor/district. Always verify on the source site before
contacting.</p>
{unsub}
</body></html>"""


def _plain_summary(main_deals, still_active, comparison_html):
    today = date.today().isoformat()
    lines = [f"Riga flat deals {today}"]
    import re
    plain_comp = re.sub(r"<[^>]+>", " ", comparison_html)
    plain_comp = re.sub(r"\s+", " ", plain_comp).strip()
    if plain_comp:
        lines.append(plain_comp)
    for dt in config.DEAL_TYPES:
        items = main_deals.get(dt, [])
        lines.append(f"\n== {dt.upper()} - new/changed ({len(items)}) ==")
        for it in items:
            listing, score, _m, badge, detail = it
            extra = f" [{badge}" + (f" {detail}" if detail else "") + "]" if badge else ""
            lines.append(
                f"  {listing.get('district')} | {listing.get('rooms')}r "
                f"{listing.get('area_m2')}m2 {listing.get('floor')} | "
                f"{_fmt_price(listing.get('price_eur'))} | "
                f"score {score:+.2f}{extra} | {listing.get('source')} "
                f"{listing.get('url')}")
        still = still_active.get(dt, [])
        if still:
            lines.append(f"\n== Still active from yesterday - {dt} ({len(still)}) ==")
            for it in still:
                listing, score, _m = it
                lines.append(
                    f"  {listing.get('district')} | {listing.get('rooms')}r "
                    f"{listing.get('area_m2')}m2 {listing.get('floor')} | "
                    f"{_fmt_price(listing.get('price_eur'))} | "
                    f"score {score:+.2f} | {listing.get('source')} "
                    f"{listing.get('url')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------
def send(main_deals, still_active, comparison_html, status_note, price_data=None):
    """Send the digest email. Returns (sent:bool, info:str)."""
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM")
    recipient = os.environ.get("EMAIL_TO") or ""

    # always save the HTML digest first (for audit / no-SMTP fallback)
    html = build_html(main_deals, still_active, comparison_html, status_note,
                      recipient, price_data)
    today = date.today().isoformat()
    digest_path = os.path.join(config.DIGEST_DIR, f"digest_{today}.html")
    os.makedirs(config.DIGEST_DIR, exist_ok=True)
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(html)

    missing = [k for k, v in {"SMTP_HOST": host, "SMTP_USER": user,
                              "EMAIL_FROM": sender, "EMAIL_TO": recipient}.items()
               if not v]
    if missing:
        msg = (f"SMTP not configured (missing {missing}). "
               f"Digest saved to {digest_path}")
        print(f"[notifier] {msg}")
        return False, msg

    # unsubscribe check (single recipient)
    if history.is_unsubscribed(recipient):
        msg = (f"recipient {recipient} has unsubscribed - email skipped. "
               f"Digest saved to {digest_path}")
        print(f"[notifier] {msg}")
        return False, msg

    n_main = sum(len(v) for v in main_deals.values())
    n_still = sum(len(v) for v in still_active.values())
    subject = (f"{config.EMAIL_SUBJECT_PREFIX} - {today} "
               f"({n_main} new/changed, {n_still} still active)")
    mm = MIMEMultipart("alternative")
    mm["Subject"] = subject
    mm["From"] = sender
    mm["To"] = recipient
    mm.attach(MIMEText(_plain_summary(main_deals, still_active, comparison_html),
                       "plain", "utf-8"))
    mm.attach(MIMEText(html, "html", "utf-8"))

    try:
        port_i = int(port) if port else 587
        if port_i == 465:
            srv = smtplib.SMTP_SSL(host, port_i, timeout=30)
        else:
            srv = smtplib.SMTP(host, port_i, timeout=30)
            srv.starttls()
        if pwd:
            srv.login(user, pwd)
        srv.sendmail(sender, recipient.split(","), mm.as_string())
        srv.quit()
        print(f"[notifier] email sent to {recipient}: {subject}")
        return True, f"email sent to {recipient}"
    except Exception as e:
        msg = f"email send failed: {e}. Digest saved to {digest_path}"
        print(f"[notifier] {msg}")
        return False, msg


# ---------------------------------------------------------------------------
# ESCALATION ALERT (hourly hot-deal instant email)
# ---------------------------------------------------------------------------
def _alert_row_html(listing, score):
    score_str = f"{score:+.2f}" if score is not None else "-"
    return (
        "<tr>"
        f"<td style='text-align:center;font-size:20px;font-weight:bold;color:#e74c3c'>{score_str}</td>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right;font-weight:bold'>{_fmt_price(listing.get('price_eur'))}</td>"
        f"<td style='text-align:right'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')} &rarr;</a></td>"
        "</tr>"
    )


def build_alert_html(hot_deals, threshold):
    """hot_deals: list of (listing, score, method) tuples that exceeded threshold."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = "".join(_alert_row_html(l, s) for l, s, _ in hot_deals)
    n = len(hot_deals)
    unsub = _unsubscribe_html(os.environ.get("EMAIL_TO", ""))
    browser = ""
    if config.SITE_URL:
        browser = (f'<p style="font-size:13px"><a href="{config.SITE_URL}'
                   '">View all deals in browser</a></p>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;color:#222;max-width:700px;margin:0 auto}}
h2{{color:#c0392b}}td,th{{border:1px solid #ddd;padding:8px}}a{{color:#2874a6}}
.note{{color:#777;font-size:12px}}
.alert{{background:#fdedee;border:2px solid #e74c3c;border-radius:8px;padding:15px;margin:15px 0}}
</style></head><body>
<h2>&#x1F6A8; HOT DEAL ALERT — {n} deal(s) spotted</h2>
<p style="color:#666">Scanned at {now} &middot; Threshold: score &ge; {threshold}</p>
{browser}
<div class="alert">
<p style="font-size:16px"><strong>These deals are statistical outliers — significantly cheaper
than the model expects for their characteristics.</strong> Act fast; competitive markets move
quickly.</p>
<table style='border-collapse:collapse;width:100%;font-size:14px'>
<tr style='background:#f8d7da'>
<th>Deal score</th><th>District</th><th>Rooms</th><th>m2</th><th>Floor</th>
<th style='text-align:right'>Price</th><th style='text-align:right'>EUR/m2</th><th>Link</th></tr>
{rows}</table>
</div>
<hr><p class="note">This is an automated hourly escalation alert from Flat_Searcher.
The daily digest is sent separately. Higher deal score = cheaper than expected.
Always verify on the source site before contacting.</p>
{unsub}
</body></html>"""


def _alert_plain(hot_deals, threshold):
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"HOT DEAL ALERT - {len(hot_deals)} deal(s) spotted at {now}",
             f"Threshold: score >= {threshold}\n"]
    for l, s, _ in hot_deals:
        lines.append(f"  score {s:+.2f} | {l.get('district')} | {l.get('rooms')}r "
                     f"{l.get('area_m2')}m2 {l.get('floor')} | "
                     f"{_fmt_price(l.get('price_eur'))} | "
                     f"{_fmt_ppu(l.get('price_per_m2'))} | "
                     f"{l.get('source')} {l.get('url')}")
    return "\n".join(lines)


def send_alert(hot_deals, threshold):
    """Send an instant escalation alert email. Returns (sent:bool, info:str)."""
    if not hot_deals:
        return False, "no hot deals to alert"

    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM")
    recipient = os.environ.get("EMAIL_TO") or ""

    html = build_alert_html(hot_deals, threshold)
    today = date.today().isoformat()
    alert_path = os.path.join(config.DIGEST_DIR, f"alert_{today}.html")
    os.makedirs(config.DIGEST_DIR, exist_ok=True)
    with open(alert_path, "a", encoding="utf-8") as f:
        f.write(html + "\n<hr>\n")

    missing = [k for k, v in {"SMTP_HOST": host, "SMTP_USER": user,
                              "EMAIL_FROM": sender, "EMAIL_TO": recipient}.items()
               if not v]
    if missing:
        msg = (f"SMTP not configured (missing {missing}). "
               f"Alert saved to {alert_path}")
        print(f"[notifier] {msg}")
        return False, msg

    if history.is_unsubscribed(recipient):
        msg = f"recipient {recipient} has unsubscribed - alert skipped"
        print(f"[notifier] {msg}")
        return False, msg

    n = len(hot_deals)
    subject = f"{config.EMAIL_SUBJECT_PREFIX} - HOT DEAL ALERT ({n} deal{'s' if n != 1 else ''})"
    mm = MIMEMultipart("alternative")
    mm["Subject"] = subject
    mm["From"] = sender
    mm["To"] = recipient
    mm.attach(MIMEText(_alert_plain(hot_deals, threshold), "plain", "utf-8"))
    mm.attach(MIMEText(html, "html", "utf-8"))

    try:
        port_i = int(port) if port else 587
        if port_i == 465:
            srv = smtplib.SMTP_SSL(host, port_i, timeout=30)
        else:
            srv = smtplib.SMTP(host, port_i, timeout=30)
            srv.starttls()
        if pwd:
            srv.login(user, pwd)
        srv.sendmail(sender, recipient.split(","), mm.as_string())
        srv.quit()
        print(f"[notifier] ALERT email sent to {recipient}: {subject}")
        return True, f"alert email sent to {recipient}"
    except Exception as e:
        msg = f"alert email send failed: {e}. Alert saved to {alert_path}"
        print(f"[notifier] {msg}")
        return False, msg


# ---------------------------------------------------------------------------
# OPERATOR HEALTH ALERT (goes to you, not the deal recipient)
# ---------------------------------------------------------------------------
def send_ops_alert(issue_key, message, source_counts, total, context="daily"):
    """Email the OPERATOR that the pipeline looks unhealthy.

    Recipient is OPS_EMAIL_TO, falling back to EMAIL_FROM (i.e. yourself), so a
    broken scraper never spams the flat-hunting recipient. Returns (sent, info).
    """
    from datetime import datetime

    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM")
    ops_to = os.environ.get("OPS_EMAIL_TO") or sender

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not host or not user or not sender or not ops_to:
        return False, ("SMTP/OPS_EMAIL_TO not configured - health issue logged "
                       "only")

    subject = f"[Flat_Searcher] HEALTH: {issue_key} ({context} run)"
    body = (f"Flat_Searcher health alert\n"
            f"==========================\n\n"
            f"Time:    {now}\n"
            f"Run:     {context}\n"
            f"Issue:   {issue_key}\n"
            f"Totals:  {total} listing(s) - per source: {source_counts}\n\n"
            f"{message}\n\n"
            f"This alert is throttled to once per issue per day.\n"
            f"It was sent to the operator address, not the digest recipient.\n")

    mm = MIMEMultipart("alternative")
    mm["Subject"] = subject
    mm["From"] = sender
    mm["To"] = ops_to
    mm.attach(MIMEText(body, "plain", "utf-8"))

    try:
        port_i = int(port) if port else 587
        if port_i == 465:
            srv = smtplib.SMTP_SSL(host, port_i, timeout=30)
        else:
            srv = smtplib.SMTP(host, port_i, timeout=30)
            srv.starttls()
        if pwd:
            srv.login(user, pwd)
        srv.sendmail(sender, ops_to.split(","), mm.as_string())
        srv.quit()
        print(f"[notifier] ops health alert sent to {ops_to}: {issue_key}")
        return True, f"ops alert sent to {ops_to}"
    except Exception as e:
        return False, f"ops alert send failed: {e}"
