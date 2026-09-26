# -*- coding: utf-8 -*-
"""
Car digest HTML builder — website only (no car email).

build_html(qualified, assessed, source_counts, source_errors, badges,
run_date) -> str
"""
import html
from datetime import date, datetime
from urllib.parse import urlparse

import config

ALLOWED_HOSTS = {"www.ss.com", "ss.com", "pp.lv", "www.pp.lv"}

BADGE_STYLES = {
    "NEW": "background:#27ae60;color:#fff",
    "PRICE DROP": "background:#c0392b;color:#fff",
    "STILL ACTIVE": "background:#7f8c8d;color:#fff",
    "REAPPEARED": "background:#e67e22;color:#fff",
}


def _riga_stamp(run_date):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Riga")).strftime("%Y-%m-%d %H:%M") + " Riga time"
    except Exception:
        return str(run_date)


def _safe_url(url):
    """Return the URL only if it is an https link to an allow-listed host."""
    try:
        p = urlparse(str(url or ""))
    except Exception:
        return None
    if p.scheme == "https" and p.netloc.lower() in ALLOWED_HOSTS:
        return str(url)
    return None


def _link(url, text):
    u = _safe_url(url)
    label = html.escape(str(text))
    if not u:
        return label
    return f'<a href="{html.escape(u, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _e(value):
    return html.escape("" if value is None else str(value))


def _fmt(value, suffix=""):
    return f"{_e(value)}{suffix}" if value is not None else "—"


def _fmt_eur(value):
    if value is None:
        return "—"
    try:
        return f"€{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "—"


def _spec_text(l):
    parts = [
        str(l.get("year") or "?"),
        l.get("fuel") or "?",
        f"{l.get('engine_l')}L" if l.get("engine_l") else "?L",
        f"{int(l['mileage_km'] / 1000)}k km" if isinstance(l.get("mileage_km"), (int, float)) else "? km",
    ]
    extras = [x for x in (l.get("gearbox"), l.get("body"), l.get("location")) if x]
    return _e(" · ".join(parts + [str(x) for x in extras]))


def _badge_html(key, badges):
    b = badges.get(key)
    if not b:
        return ""
    style = BADGE_STYLES.get(b, "background:#555;color:#fff")
    return (f'<span style="{style};padding:2px 6px;border-radius:3px;'
            f'font-size:11px;font-weight:bold">{_e(b)}</span>')


def _listing_links(l):
    key_html = _link(l.get("url"), l.get("source") or "ad")
    for extra in l.get("also_on") or []:
        key_html += " + " + _link(extra.get("url"), extra.get("source") or "ad")
    return key_html


def _row(l, badges):
    key = f"{l.get('source')}:{l.get('id')}"
    title = l.get("title") or f"{l.get('make', '')} {l.get('model', '')}"
    cautions = []
    mileage = l.get("mileage_km")
    year = l.get("year")
    if isinstance(mileage, (int, float)) and \
            mileage >= config.CAR_HIGH_MILEAGE_WARNING_KM:
        cautions.append("High mileage — budget for repairs")
    if isinstance(year, int) and \
            year <= date.today().year - config.CAR_AGE_WARNING_YEARS:
        cautions.append("Older car — inspect carefully")
    caution_html = ""
    if cautions:
        caution_html = ("<br><span style='color:#a04000;font-size:12px'>"
                        f"{_e('; '.join(cautions))}</span>")
    cells = [
        f"<td style='padding:6px'>{_e(title)}<br>"
        f"<span style='color:#777;font-size:12px'>{_e(l.get('make'))} {_e(l.get('model'))} — {_spec_text(l)}</span><br>"
        f"{_badge_html(key, badges)} {_listing_links(l)}{caution_html}</td>",
        f"<td style='padding:6px;text-align:right'><b>{_fmt_eur(l.get('price_eur'))}</b></td>",
        f"<td style='padding:6px;text-align:right'>{_fmt_eur(l.get('_median'))}</td>",
        f"<td style='padding:6px;text-align:center'>{_fmt(l.get('_comps'))}</td>",
        f"<td style='padding:6px;text-align:right'>{_fmt_eur(l.get('_savings'))}</td>",
        f"<td style='padding:6px;text-align:right'>{_fmt(l.get('_discount_pct'), '%')}</td>",
        f"<td style='padding:6px;text-align:center'><b>{_fmt(l.get('_score'))}</b></td>",
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _coverage_html(source_counts, source_errors):
    bits = []
    for name in ("ss.com", "pp.lv"):
        c = (source_counts or {}).get(name) or {}
        bits.append(f"{_e(name)}: {int(c.get('raw') or 0)} scraped, "
                    f"{int(c.get('eligible') or 0)} eligible")
    html_bits = "; ".join(bits)
    err = ""
    if source_errors:
        items = "".join(f"<li><b>{_e(src)}</b>: {_e(msg)}</li>"
                        for src, msg in source_errors.items())
        err = (f"<div style='background:#fdecea;border:1px solid #c0392b;"
               f"padding:10px 14px;margin:12px 0'>"
               f"<b>Warning: source outage — coverage is incomplete:</b><ul>{items}</ul></div>")
    reasons = ""
    drop = (source_counts or {}).get("_drop_reasons")
    if drop:
        reasons = ("<p class='note'>Dropped as ineligible: "
                   + ", ".join(f"{_e(k)} ×{v}" for k, v in sorted(drop.items()))
                   + "</p>")
    return html_bits, err, reasons


def build_html(qualified, assessed, source_counts, source_errors, badges, run_date):
    qualified = qualified or []
    assessed = assessed or []
    badges = badges or {}
    stamp = _riga_stamp(run_date)
    coverage, error_html, drop_html = _coverage_html(source_counts, source_errors)
    both_failed = source_errors and all(
        name in source_errors for name in ("ss.com", "pp.lv"))

    qualified_keys = {f"{l.get('source')}:{l.get('id')}" for l in qualified}
    b7_watch = sorted(
        (l for l in assessed
         if l.get("model") == "passat-b7"
         and f"{l.get('source')}:{l.get('id')}" not in qualified_keys),
        key=lambda l: (abs((l.get("mileage_km") or 0)
                           - config.CAR_PASSAT_REFERENCE_MILEAGE_KM),
                       l.get("price_eur") or 0)
    )[:config.CAR_B7_WATCH_N]

    if both_failed:
        top_html = (
            "<h2 style='color:#c0392b'>Warning: no current data — both sources failed</h2>"
            "<p>Today's car scan could not reach either source, so there is "
            "<b>no fresh data</b> in this digest. This page is generated for "
            f"{_e(run_date)} and intentionally shows no listings rather than "
            "recycling an older digest.</p>")
    elif not qualified:
        top_html = (
            "<h2>No qualifying deals today</h2>"
            "<p>No listing is currently priced at least "
            f"{_e(config.CAR_GOOD_MIN_DISCOUNT_PCT)}% (≥{_fmt_eur(config.CAR_GOOD_MIN_SAVINGS_EUR)}) "
            "below the median of "
            f"{_e(config.CAR_MIN_COMPARABLES)}+ comparable asking prices, or "
            "there were too few comparable listings to compute a median. "
            f"Coverage: {coverage}.</p>")
    else:
        rows = "".join(_row(l, badges) for l in qualified)
        top_html = (
            f"<h2>All qualifying deals ({len(qualified)})</h2>"
            "<table><tr style='background:#f0f0f0'>"
            "<th style='text-align:left;padding:6px'>Listing</th>"
            "<th style='padding:6px'>Asking</th>"
            "<th style='padding:6px'>Median ask</th>"
            "<th style='padding:6px'>Comps</th>"
            "<th style='padding:6px'>Discount €</th>"
            "<th style='padding:6px'>Discount %</th>"
            "<th style='padding:6px'>Score</th></tr>"
            f"{rows}</table>")

    if b7_watch:
        brows = "".join(_row(l, badges) for l in b7_watch)
        b7_table = (
            "<table><tr style='background:#f0f0f0'>"
            "<th style='text-align:left;padding:6px'>Listing</th>"
            "<th style='padding:6px'>Asking</th>"
            "<th style='padding:6px'>Median ask</th>"
            "<th style='padding:6px'>Comps</th>"
            "<th style='padding:6px'>Discount €</th>"
            "<th style='padding:6px'>Discount %</th>"
            "<th style='padding:6px'>Score</th></tr>"
            f"{brows}</table>")
    else:
        b7_table = ("<p class='note'>No additional in-budget Passat B7 "
                    "listings met the minimum data requirements in today's "
                    "sample.</p>")
    b7_html = (
        "<h2>Passat B7 watch — context, not vetted deals</h2>"
        "<p class='note'>Assessed Passat B7s outside the qualifying-deals "
        "list (closest to the reference mileage first), including unrated "
        "ones — when fewer than four comparable ads are available. These "
        "are shown for context only — they have <b>not</b> passed the deal "
        "test.</p>"
        f"<p class='note'>The acquaintance's B7 "
        f"(~{_fmt_eur(config.CAR_PASSAT_REFERENCE_PRICE_EUR)}, "
        f"~{config.CAR_PASSAT_REFERENCE_MILEAGE_KM // 1000}k km) sits above "
        f"the provisional {_fmt_eur(config.CAR_PRICE_CEILING_EUR)} cap and "
        "<b>cannot be appraised</b> without its exact year, engine, fuel, "
        "gearbox and documented history; there is no reliable basis here to "
        "call any specific price 'fair' for it.</p>"
        f"{b7_table}")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga car deals — {_e(run_date)}</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:960px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}h2{{color:#2874a6;margin-top:28px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
td,th{{border-bottom:1px solid #eee}}
a{{color:#2874a6;text-decoration:none}}a:hover{{text-decoration:underline}}
.note{{color:#777;font-size:13px}}
.box{{background:#f7f9fb;border:1px solid #dbe4ea;padding:10px 14px;margin:12px 0}}
</style></head><body>
<h1>Riga car deals — {_e(stamp)}</h1>
<p class="note">Coverage: {coverage}. ss.com: only the newest
{_e(config.CAR_SS_MAX_PAGES_PER_MAKE)} pages per make plus
{_e(config.CAR_SS_MAX_B7_PAGES)} dedicated Passat B7 pages; pp.lv: newest
{_e(config.CAR_PP_MAX_PAGES)} pages. This is <b>not</b> an exhaustive scan
of either site.</p>
{error_html}
{drop_html}
<div class="box">
<b>Budget context (provisional):</b> on a ~€1,200/month income the
provisional purchase ceiling is {_fmt_eur(config.CAR_PRICE_CEILING_EUR)}
plus a <b>separate</b> {_fmt_eur(config.CAR_REPAIR_RESERVE_EUR)} reserve
intended for the pre-purchase inspection, initial repairs and
registration. Ongoing fuel, taxes, insurance and maintenance are
<b>additional</b> recurring expenses on top of that reserve. This ceiling
is a working assumption — not a guarantee of affordability and not
financing advice.
</div>
<div class="box">
<b>Eligibility:</b> only seller ads asking
{_fmt_eur(config.CAR_MIN_PRICE_EUR)}–{_fmt_eur(config.CAR_COMPARABLE_MAX_PRICE_EUR)}
with year ≥{_e(config.CAR_MIN_YEAR)} and mileage
≤{config.CAR_MAX_MILEAGE_KM:,} km, identified fuel (and engine where
applicable), are considered; candidates are capped at the provisional
{_fmt_eur(config.CAR_PRICE_CEILING_EUR)} ceiling, while comparable asking
prices are read up to {_fmt_eur(config.CAR_COMPARABLE_MAX_PRICE_EUR)}.
Ads without reliable specs are excluded or left unrated.
</div>
<div class="box">
<b>How a "top deal" is decided:</b> a listing qualifies only when its asking
price is at least {_e(config.CAR_GOOD_MIN_DISCOUNT_PCT)}% and
{_fmt_eur(config.CAR_GOOD_MIN_SAVINGS_EUR)} below the median of at least
{_e(config.CAR_MIN_COMPARABLES)} distinct comparable <i>current asking
prices</i>. Comparables must match on make/model (incl. Passat generations
like B7), fuel, year ±{_e(config.CAR_YEAR_TOLERANCE)}, mileage
±{config.CAR_MILEAGE_TOLERANCE_KM:,} km, engine
±{_e(config.CAR_ENGINE_TOLERANCE_L)}L, and gearbox/body whenever both
sides state them. Score is 0–100: 50 at the median, +2 points per 1%
cheaper, capped at 100. Missing gearbox/body reduce confidence in the
match. Asking prices are not final selling prices, and mechanical/service
condition cannot be verified from a listing.
</div>
{top_html}
{b7_html}
<div class="box">
<b>Before buying:</b> check mileage and history in the CSDD register
(e.csdd.lv), get an independent mechanical inspection, and verify all
documentation (registration, service records, outstanding finance).
</div>
<hr><p class="note">Generated by Flat_Searcher car digest — website only,
no email is sent for cars.</p>
</body></html>"""
