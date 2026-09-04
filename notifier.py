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
        timeline_row = (f'<tr class="timeline-row"><td colspan="13" style="padding:2px 5px;'
                        f'border-top:none;background:#fafafa">{timeline_html}</td></tr>')
    listed_date, days_market, first_price, change_pct = _get_listing_age(listing, price_data)
    # Numeric sort values
    price_val = listing.get('price_eur', 0) or 0
    ppu_val = listing.get('price_per_m2', 0) or 0
    score_val = score if score is not None else -999
    days_val = int(days_market) if days_market and days_market.lstrip('-').isdigit() else -1
    # Color for change percentage
    change_color = '#666'
    if change_pct:
        if change_pct.startswith('-'):
            change_color = '#27ae60'  # green = price dropped
        elif change_pct.startswith('+'):
            change_color = '#e74c3c'  # red = price increased
    return (
        "<tr>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center' data-sort='{listing.get('rooms',0) or 0}'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center' data-sort='{listing.get('area_m2',0) or 0}'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:center' data-sort='{score_val}'>{score_str}</td>"
        f"<td>{_badge_html(badge, detail)}</td>"
        f"<td style='text-align:center;font-size:12px;color:#666' data-sort='{listed_date}'>{listed_date}</td>"
        f"<td style='text-align:center;font-size:12px;color:#666' data-sort='{days_val}'>{days_market}</td>"
        f"<td style='text-align:right;font-size:12px;color:#666'>{first_price}</td>"
        f"<td style='text-align:center;font-size:12px;color:{change_color}'>{change_pct}</td>"
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
        timeline_row = (f'<tr class="timeline-row"><td colspan="13" style="padding:2px 5px;'
                        f'border-top:none;background:#fafafa">{timeline_html}</td></tr>')
    listed_date, days_market, first_price, change_pct = _get_listing_age(listing, price_data)
    price_val = listing.get('price_eur', 0) or 0
    ppu_val = listing.get('price_per_m2', 0) or 0
    score_val = score if score is not None else -999
    days_val = int(days_market) if days_market and days_market.lstrip('-').isdigit() else -1
    change_color = '#666'
    if change_pct:
        if change_pct.startswith('-'):
            change_color = '#27ae60'
        elif change_pct.startswith('+'):
            change_color = '#e74c3c'
    return (
        "<tr>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center' data-sort='{listing.get('rooms',0) or 0}'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center' data-sort='{listing.get('area_m2',0) or 0}'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:center' data-sort='{score_val}'>{score_str}</td>"
        f"<td></td>"
        f"<td style='text-align:center;font-size:12px;color:#666' data-sort='{listed_date}'>{listed_date}</td>"
        f"<td style='text-align:center;font-size:12px;color:#666' data-sort='{days_val}'>{days_market}</td>"
        f"<td style='text-align:right;font-size:12px;color:#666'>{first_price}</td>"
        f"<td style='text-align:center;font-size:12px;color:{change_color}'>{change_pct}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')}</a></td>"
        "</tr>"
        f"{timeline_row}"
    )


def _get_listing_age(listing, price_data=None):
    """Return (listed_date_str, days_on_market_str, first_price_str, price_change_pct_str)
    for a listing.

    Uses CenuMednieks first_listed_date + original_price if available, otherwise
    falls back to our own first observation date + price.
    Returns ('', '', '', '') if no data.
    """
    from datetime import date as _date

    if price_data is None:
        return '', '', '', ''

    key = f"{listing.get('source')}:{listing.get('id')}"
    entry = price_data.get(key, {})
    cenu = entry.get('cenumednieks')
    current_price = listing.get('price_eur')

    # Prefer CenuMednieks first_listed_date (true original listing date)
    if cenu and cenu.get('first_listed_date'):
        listed = cenu['first_listed_date']
        days = cenu.get('days_on_market')
        first_price = cenu.get('original_price')
        if days is None:
            try:
                d = _date.fromisoformat(listed[:10])
                days = (_date.today() - d).days
            except ValueError:
                days = None
        # Calculate price change percentage
        change_pct = ''
        if first_price and current_price and first_price > 0:
            pct = ((current_price - first_price) / first_price) * 100
            change_pct = f"{pct:+.1f}%"
        return (listed[:10],
                str(days) if days is not None else '',
                _fmt_price(first_price) if first_price else '',
                change_pct)

    # Fall back to our own first observation
    our = entry.get('our_tracking', [])
    if our:
        first_date = our[0].get('date', '')
        first_price = our[0].get('price')
        days = None
        try:
            d = _date.fromisoformat(first_date[:10])
            days = (_date.today() - d).days
        except ValueError:
            pass
        change_pct = ''
        if first_price and current_price and first_price > 0:
            pct = ((current_price - first_price) / first_price) * 100
            change_pct = f"{pct:+.1f}%"
        return (first_date[:10],
                str(days) if days is not None else '',
                _fmt_price(first_price) if first_price else '',
                change_pct)

    return '', '', '', ''


def _table_header(extra_col="Status", sortable_id=""):
    """Build table header. sortable_id is a unique id for the table (for JS sorting)."""
    sort_attr = f" onclick=\"sortTable('{sortable_id}',{{col}})\" style='cursor:pointer'" if sortable_id else ""
    sort_marker = " &#8661;" if sortable_id else ""
    return (
        f"<table id='{sortable_id}' style='border-collapse:collapse;width:100%;font-size:14px' data-sortable='1'>"
        "<tr style='background:#f0f0f0'>"
        f"<th style='text-align:left;padding:4px'{sort_attr.format(col=0)}>District{sort_marker}</th>"
        f"<th{sort_attr.format(col=1)}>Rooms{sort_marker}</th>"
        f"<th{sort_attr.format(col=2)}>m2{sort_marker}</th>"
        f"<th{sort_attr.format(col=3)}>Floor{sort_marker}</th>"
        f"<th style='text-align:right'{sort_attr.format(col=4)}>Price{sort_marker}</th>"
        f"<th style='text-align:right'{sort_attr.format(col=5)}>EUR/m2{sort_marker}</th>"
        f"<th{sort_attr.format(col=6)}>Deal score{sort_marker}</th>"
        f"<th>{extra_col}</th>"
        f"<th{sort_attr.format(col=8)}>Listed{sort_marker}</th>"
        f"<th{sort_attr.format(col=9)}>Days{sort_marker}</th>"
        f"<th style='text-align:right'{sort_attr.format(col=10)}>First price{sort_marker}</th>"
        f"<th{sort_attr.format(col=11)}>Change{sort_marker}</th>"
        f"<th>Source</th></tr>"
    )


def _main_section_html(title, items, subtitle, price_data=None, table_id=""):
    if not items:
        return (f"<h3>{title}</h3>"
                f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
                "<p>No new or changed qualifying deals today.</p>")
    rows = "".join(_main_row_html(it, price_data) for it in items)
    return (
        f"<h3>{title}</h3>"
        f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
        f"{_table_header('Status', table_id)}{rows}</table>"
    )


def _still_active_section_html(deal_type, items, price_data=None, table_id=""):
    if not items:
        return ""
    rows = "".join(_still_row_html(it, price_data) for it in items)
    return (
        f"<h3 style='color:#888'>Still active from yesterday — {deal_type}</h3>"
        "<p style='color:#999;font-size:12px'>These deals were in yesterday's "
        "digest and are still among the best today. No action needed unless "
        "you missed them.</p>"
        f"<div style='opacity:0.85'>{_table_header('', table_id)}{rows}</table></div>"
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
def build_html(main_deals, still_active, comparison_html, status_note,
               recipient="", price_data=None, map_markers=None,
               exceptional_html=""):
    today = date.today().isoformat()
    sections = []

    for dt in config.DEAL_TYPES:
        items = main_deals.get(dt, [])
        subtitle = (f"Top {len(items)} {'new / changed' if items else ''} "
                    f"{dt} deals ranked best-first. Deal score = how much "
                    f"cheaper than the model expects (higher = better deal). "
                    f"Click column headers to sort.")
        sections.append(_main_section_html(dt.upper(), items, subtitle,
                                            price_data, f"tbl_main_{dt}"))

        still = still_active.get(dt, [])
        sa = _still_active_section_html(dt, still, price_data, f"tbl_still_{dt}")
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

    # Map section (Leaflet.js with OpenStreetMap tiles — free, no API key)
    map_html = _build_map_html(map_markers) if map_markers else ""

    has_map = bool(map_markers)
    body_class = ' class="has-map"' if has_map else ''

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:900px;margin:0 auto;padding:0 16px}}
h2{{color:#1a5276}}h3{{color:#2874a6;border-bottom:2px solid #2874a6;padding-bottom:4px}}
td,th{{border:1px solid #ddd;padding:5px}}a{{color:#2874a6}}
.note{{color:#777;font-size:12px}}
th{{user-select:none}}th:hover{{background:#e8e8e8}}
.timeline-row td{{border:none;padding:2px 5px}}

/* Floating map sidebar */
#map-sidebar{{
  position:fixed;top:0;right:0;width:380px;height:100vh;z-index:1000;
  background:#fff;border-left:1px solid #ddd;box-shadow:-2px 0 8px rgba(0,0,0,0.1);
  display:flex;flex-direction:column;transition:transform 0.3s ease;
}}
#map-sidebar.hidden{{transform:translateX(380px)}}
#map-sidebar .map-header{{
  padding:10px 14px;background:#1a5276;color:#fff;font-size:14px;
  display:flex;justify-content:space-between;align-items:center;flex-shrink:0;
}}
#map-sidebar .map-header b{{font-size:15px}}
#map-sidebar .map-toggle{{
  background:rgba(255,255,255,0.2);border:none;color:#fff;cursor:pointer;
  padding:4px 10px;border-radius:4px;font-size:12px;
}}
#map-sidebar .map-toggle:hover{{background:rgba(255,255,255,0.3)}}
#map-sidebar #map{{flex:1;width:100%;height:auto;border:none}}

/* Floating button when sidebar is hidden */
#map-float-btn{{
  position:fixed;top:20px;right:20px;z-index:1001;
  background:#1a5276;color:#fff;border:none;cursor:pointer;
  padding:10px 16px;border-radius:8px;font-size:14px;font-weight:bold;
  box-shadow:0 2px 8px rgba(0,0,0,0.2);display:none;
}}
#map-float-btn:hover{{background:#2874a6}}
#map-sidebar.hidden ~ #map-float-btn{{display:block}}

/* On wide screens, shift content left to make room for sidebar */
@media(min-width:1200px){{
  body.has-map{{margin-right:380px;max-width:calc(900px + 380px)}}
  #map-float-btn{{display:none !important}}
  #map-sidebar .map-toggle{{display:none}}
}}
/* On narrow screens, sidebar overlays content */
@media(max-width:1199px){{
  #map-sidebar{{width:320px}}
  #map-sidebar.hidden{{transform:translateX(320px)}}
}}
</style>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      crossorigin=""/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        crossorigin=""></script>
<script>
// Click-to-sort table headers. Keeps timeline rows attached to their parent row.
var sortState = {{}};
function sortTable(tableId, colIdx) {{
  var table = document.getElementById(tableId);
  if (!table) return;
  var rows = Array.from(table.querySelectorAll('tr')).slice(1);
  var groups = [];
  for (var i = 0; i < rows.length; i++) {{
    if (rows[i].classList.contains('timeline-row')) {{
      if (groups.length) groups[groups.length-1].push(rows[i]);
    }} else {{
      groups.push([rows[i]]);
    }}
  }}
  var key = tableId + '_' + colIdx;
  sortState[key] = !sortState[key];
  var asc = sortState[key];
  groups.sort(function(a, b) {{
    var va = a[0].children[colIdx].getAttribute('data-sort');
    var vb = b[0].children[colIdx].getAttribute('data-sort');
    if (va === null || vb === null) return 0;
    va = va.trim(); vb = vb.trim();
    var na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) {{
      return asc ? na - nb : nb - na;
    }}
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});
  for (var g = 0; g < groups.length; g++) {{
    for (var r = 0; r < groups[g].length; r++) {{
      table.appendChild(groups[g][r]);
    }}
  }}
}}
function toggleMap() {{
  var sb = document.getElementById('map-sidebar');
  sb.classList.toggle('hidden');
  if (!sb.classList.contains('hidden') && window._leafletMap) {{
    setTimeout(function(){{ window._leafletMap.invalidateSize(); }}, 300);
  }}
}}
</script>
</head><body{body_class}>
{map_html}
<h2>Riga flat deals - {today}</h2>
{browser_link}
<p>Districts: {', '.join(config.DISTRICTS.keys())} &middot; Sources: ss.com, city24.lv</p>
<p class="note">Scoring: {status_note}</p>
{comparison_html}
{exceptional_html}
{body_sections}
<hr><p class="note">Generated by Flat_Searcher. Higher deal score = cheaper than
expected for its size/floor/district. Always verify on the source site before
contacting.</p>
{unsub}
</body></html>"""


def _build_map_html(markers):
    """Build a floating sidebar map with markers for each listing.

    The map is position:fixed on the right side, always visible while
    scrolling. On wide screens (≥1200px) the content shifts left to
    make room. On narrow screens, a toggle button shows/hides the sidebar.

    Markers are color-coded by deal type:
      - rent = blue
      - sale = orange
    Clicking a marker shows a popup with listing details and a link.
    """
    if not markers:
        return ""

    import json as _json

    center_lat, center_lon = 56.95, 24.10
    zoom = 12

    js_markers = []
    for m in markers:
        color = "#2874a6" if m.get("deal_type") == "rent" else "#e67e22"
        js_markers.append({
            "lat": m["lat"],
            "lon": m["lon"],
            "popup": m["popup"],
            "color": color,
            "deal_type": m.get("deal_type", ""),
        })

    js_data = _json.dumps(js_markers, ensure_ascii=False)
    n_markers = len(js_markers)

    return f"""
<!-- Floating map sidebar -->
<div id="map-sidebar">
  <div class="map-header">
    <b>Map ({n_markers} listings)</b>
    <button class="map-toggle" onclick="toggleMap()">Hide</button>
  </div>
  <div id="map"></div>
</div>
<button id="map-float-btn" onclick="toggleMap()">Show map</button>
<script>
(function() {{
  var map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});
  window._leafletMap = map;
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  }}).addTo(map);

  var markers = {js_data};
  markers.forEach(function(m) {{
    var circle = L.circleMarker([m.lat, m.lon], {{
      radius: 8,
      fillColor: m.color,
      color: '#fff',
      weight: 2,
      opacity: 1,
      fillOpacity: 0.8
    }}).addTo(map);
    circle.bindPopup(m.popup);
  }});

  // Fit bounds to show all markers
  if (markers.length > 0) {{
    var bounds = L.latLngBounds(markers.map(function(m){{ return [m.lat, m.lon]; }}));
    map.fitBounds(bounds, {{padding: [30, 30]}});
  }}

  // Invalidate size after load (sidebar may need a moment)
  setTimeout(function(){{ map.invalidateSize(); }}, 500);
}})();
</script>
"""


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
def send(main_deals, still_active, comparison_html, status_note,
         price_data=None, map_markers=None, exceptional_html=""):
    """Send the digest email. Returns (sent:bool, info:str)."""
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM")
    recipient = os.environ.get("EMAIL_TO") or ""

    # always save the HTML digest first (for audit / no-SMTP fallback)
    html = build_html(main_deals, still_active, comparison_html, status_note,
                      recipient, price_data, map_markers, exceptional_html)
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
