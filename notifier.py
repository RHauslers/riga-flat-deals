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
def _fmt_price(v, price_unit=None):
    try:
        unit = f"/{price_unit}" if price_unit and price_unit != "mon" else ""
        return f"{float(v):,.0f} EUR{unit}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def _fmt_ppu(v):
    try:
        return f"{float(v):,.1f} EUR/m²".replace(",", " ")
    except (TypeError, ValueError):
        return ""


_BADGE_HTML = {
    "NEW": "<span style='color:#27ae60;font-weight:bold'>NEW</span>",
    "PRICE_DROP": "<span style='color:#e67e22;font-weight:bold'>PRICE DROP</span>",
    "REAPPEARED": "<span style='color:#2980b9'>REAPPEARED</span>",
    "SHORT_TERM": "<span style='color:#8e44ad;font-weight:bold'>SHORT-TERM/DAILY</span>",
}


def _badge_html(badge, detail):
    b = _BADGE_HTML.get(badge, badge or "")
    if detail:
        b += f" <span style='color:#999;font-size:11px'>({detail})</span>"
    return b


# ---------------------------------------------------------------------------
# table builders
# ---------------------------------------------------------------------------
def _change_color(change_pct):
    """Return CSS color for a change percentage string. Grey for zero."""
    if not change_pct:
        return '#666'
    # Parse the numeric value to distinguish real changes from +0.0%
    import re as _re
    m = _re.search(r'([+-]?\d+\.?\d*)', change_pct)
    if m:
        val = float(m.group(1))
        if abs(val) < 0.05:
            return '#666'  # grey for zero
        if val < 0:
            return '#27ae60'  # green = price dropped
        return '#e74c3c'  # red = price increased
    return '#666'


def _change_sort_val(change_pct):
    """Extract numeric sort value from change percentage string."""
    if not change_pct:
        return 0.0
    import re as _re
    m = _re.search(r'([+-]?\d+\.?\d*)', change_pct)
    return float(m.group(1)) if m else 0.0


# Column layout (11 columns after merging Listed+Days and First price+Change):
#  0 District  1 Rooms  2 m²  3 Floor  4 Price  5 EUR/m²  6 Deal score
#  7 Status   8 Listed (days)   9 First / change   10 Source
NUM_COLS = 11


def _main_row_html(item, price_data=None, row_idx=0):
    listing, score, method, badge, detail = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    zebra = ' style="background:#fafafa"' if row_idx % 2 else ''
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr class="timeline-row"{zebra}><td colspan="{NUM_COLS}" '
                        f'style="padding:6px 10px;border-top:none;'
                        f'border-bottom:1px solid #ccc;background:#f5f5f5;'
                        f'font-size:11px;line-height:1.6">{timeline_html}</td></tr>')
    listed_date, days_market, first_price, change_pct = _get_listing_age(listing, price_data)
    price_val = listing.get('price_eur', 0) or 0
    ppu_val = listing.get('price_per_m2', 0) or 0
    score_val = score if score is not None else -999
    days_val = int(days_market) if days_market and days_market.lstrip('-').isdigit() else -1
    listed_days = listed_date
    if days_market:
        listed_days = f"{listed_date} ({days_market}d)"
    first_change = first_price
    if change_pct:
        first_change = f"{first_price} {change_pct}" if first_price else change_pct
    ch_color = _change_color(change_pct)
    ch_sort = _change_sort_val(change_pct)

    # "map" link — only if the listing has coordinates
    map_link = ""
    if listing.get('lat') and listing.get('lon'):
        marker_id = f"{listing.get('source','')}:{listing.get('id','')}"
        map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                    f"return false\" style=\"font-size:11px;color:#1a5276\">map</a>")

    return (
        f"<tr{zebra}>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('rooms',0) or 0}'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('area_m2',0) or 0}'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:right'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:right;font-size:16px;font-weight:bold;color:#1a5276' data-sort='{score_val}'>{score_str}</td>"
        f"<td>{_badge_html(badge, detail)}</td>"
        f"<td style='text-align:right;font-size:12px;color:#666' data-sort='{listed_date}'>{listed_days}</td>"
        f"<td style='text-align:right;font-size:12px;color:{ch_color}' data-sort='{ch_sort}'>{first_change}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')}</a>{map_link}</td>"
        "</tr>"
        f"{timeline_row}"
    )


def _still_row_html(item, price_data=None, row_idx=0):
    listing, score, method = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    zebra = ' style="background:#fafafa"' if row_idx % 2 else ''
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr class="timeline-row"{zebra}><td colspan="{NUM_COLS}" '
                        f'style="padding:6px 10px;border-top:none;'
                        f'border-bottom:1px solid #ccc;background:#f5f5f5;'
                        f'font-size:11px;line-height:1.6">{timeline_html}</td></tr>')
    listed_date, days_market, first_price, change_pct = _get_listing_age(listing, price_data)
    price_val = listing.get('price_eur', 0) or 0
    ppu_val = listing.get('price_per_m2', 0) or 0
    score_val = score if score is not None else -999
    listed_days = listed_date
    if days_market:
        listed_days = f"{listed_date} ({days_market}d)"
    first_change = first_price
    if change_pct:
        first_change = f"{first_price} {change_pct}" if first_price else change_pct
    ch_color = _change_color(change_pct)
    ch_sort = _change_sort_val(change_pct)

    # "map" link — only if the listing has coordinates
    map_link = ""
    if listing.get('lat') and listing.get('lon'):
        marker_id = f"{listing.get('source','')}:{listing.get('id','')}"
        map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                    f"return false\" style=\"font-size:11px;color:#1a5276\">map</a>")

    return (
        f"<tr{zebra}>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('rooms',0) or 0}'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('area_m2',0) or 0}'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:right'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:right;font-size:16px;font-weight:bold;color:#1a5276' data-sort='{score_val}'>{score_str}</td>"
        f"<td style='text-align:right;font-size:12px;color:#666' data-sort='{listed_date}'>{listed_days}</td>"
        f"<td style='text-align:right;font-size:12px;color:{ch_color}' data-sort='{ch_sort}'>{first_change}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')}</a>{map_link}</td>"
        "</tr>"
        f"{timeline_row}"
    )


def _get_listing_age(listing, price_data=None):
    """Return (listed_date_str, days_on_market_str, first_price_str, price_change_pct_str)
    for a listing.

    Uses CenuMednieks first_listed_date + original_price if available, otherwise
    falls back to our own first observation date + price.
    Returns ('', '', '', '') if no data.

    Sanity-checks CenuMednieks original_price against the current price: if
    they differ by more than 5x, the original_price is likely from a different
    deal type (e.g. a sale price showing up for a rental) and is ignored.
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
        # Sanity check: skip original_price if it's wildly different from
        # current price (likely a different deal type, e.g. sale vs rent).
        if first_price and current_price and first_price > 0 and current_price > 0:
            ratio = max(first_price, current_price) / min(first_price, current_price)
            if ratio > 5.0:
                first_price = None  # discard, fall back below
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
        if first_price:
            return (listed[:10],
                    str(days) if days is not None else '',
                    _fmt_price(first_price),
                    change_pct)
        # first_price was discarded — still return the date/days but no price
        return (listed[:10],
                str(days) if days is not None else '',
                '',
                '')

    # Fall back to our own tracking.
    # Use first_seen (set once, never overwritten) for the date, and
    # our_tracking[0] for the first observed price.
    first_seen = entry.get('first_seen')
    our = entry.get('our_tracking', [])
    if first_seen or our:
        first_date = first_seen or (our[0].get('date', '') if our else '')
        first_price = our[0].get('price') if our else None
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


def _table_header(sortable_id="", has_status=True):
    """Build table header with 11 columns (Listed+Days and First+Change merged).

    sortable_id: unique id for JS sorting (empty = not sortable).
    has_status: if True, include the Status column (main deals only).
    """
    if sortable_id:
        sort_attr = " class=\"sort-th\" onclick=\"sortTable('{0}',{{col}})\"".format(sortable_id)
    else:
        sort_attr = ""
    cols = (
        f"<th style='text-align:left'{sort_attr.format(col=0)}>District</th>"
        f"<th{sort_attr.format(col=1)}>Rooms</th>"
        f"<th{sort_attr.format(col=2)}>m²</th>"
        f"<th{sort_attr.format(col=3)}>Floor</th>"
        f"<th style='text-align:right'{sort_attr.format(col=4)}>Price</th>"
        f"<th style='text-align:right'{sort_attr.format(col=5)}>EUR/m²</th>"
        f"<th style='text-align:right'{sort_attr.format(col=6)}>Deal score</th>"
    )
    if has_status:
        cols += f"<th{sort_attr.format(col=7)}>Status</th>"
        cols += (f"<th style='text-align:right'{sort_attr.format(col=8)}>Listed</th>"
                 f"<th style='text-align:right'{sort_attr.format(col=9)}>First / change</th>"
                 f"<th>Source</th>")
    else:
        cols += (f"<th style='text-align:right'{sort_attr.format(col=7)}>Listed</th>"
                 f"<th style='text-align:right'{sort_attr.format(col=8)}>First / change</th>"
                 f"<th>Source</th>")
    return (
        f"<table id='{sortable_id}' style='border-collapse:collapse;width:100%;font-size:14px' "
        f"data-sortable='1'>"
        "<tr style='background:#f0f0f0'>" + cols + "</tr>"
    )


def _main_section_html(title, items, subtitle, price_data=None, table_id=""):
    if not items:
        return (f"<h3>{title}</h3>"
                f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
                "<p>No new or changed qualifying deals today.</p>")
    rows = "".join(_main_row_html(it, price_data, idx) for idx, it in enumerate(items))
    return (
        f"<h3>{title}</h3>"
        f"<p style='color:#666;font-size:12px'>{subtitle}</p>"
        f"{_table_header(table_id, has_status=True)}{rows}</table>"
    )


def _still_active_section_html(deal_type, items, price_data=None, table_id=""):
    if not items:
        return ""
    rows = "".join(_still_row_html(it, price_data, idx) for idx, it in enumerate(items))
    return (
        f"<h3 style='color:#888'>Still active from yesterday — {deal_type}</h3>"
        "<p style='color:#999;font-size:12px'>These deals were in yesterday's "
        "digest and are still among the best today. No action needed unless "
        "you missed them.</p>"
        f"<div style='opacity:0.85'>{_table_header(table_id, has_status=False)}{rows}</table></div>"
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

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:900px;margin:0 auto;padding:0 16px}}
h2{{color:#1a5276}}h3{{color:#2874a6;border-bottom:2px solid #2874a6;padding-bottom:4px}}
td,th{{border:1px solid #ddd;padding:5px}}a{{color:#2874a6}}
.note{{color:#777;font-size:12px}}
th{{user-select:none;cursor:default;position:relative}}
th.sort-th{{cursor:pointer}}
th.sort-th:hover{{background:#e8e8e8}}
th.sort-th::after{{content:"\\21C5";font-size:10px;color:#bbb;margin-left:4px;opacity:0}}
th.sort-th:hover::after{{opacity:1}}
th.sort-asc::after{{content:"\\2191";font-size:10px;color:#1a5276;margin-left:4px;opacity:1}}
th.sort-desc::after{{content:"\\2193";font-size:10px;color:#1a5276;margin-left:4px;opacity:1}}
.timeline-row td{{border-top:none;border-bottom:1px solid #ccc;padding:6px 10px;background:#f5f5f5;font-size:11px;line-height:1.6}}

/* Inline map at bottom of page */
#map-container{{margin:20px 0;border:1px solid #ddd;border-radius:8px;overflow:hidden}}
#map-container .map-header{{padding:10px 14px;background:#1a5276;color:#fff;font-size:14px;font-weight:bold}}
#map-container #map{{width:100%;height:400px}}

/* Info overlay modal */
#info-overlay{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;
  background:rgba(0,0,0,0.5);z-index:2000;justify-content:center;align-items:center}}
#info-overlay.show{{display:flex}}
#info-box{{background:#fff;border-radius:10px;padding:28px;max-width:560px;
  max-height:80vh;overflow-y:auto;margin:16px;box-shadow:0 4px 20px rgba(0,0,0,0.3)}}
#info-box h3{{color:#1a5276;border:none;margin:0 0 12px 0;font-size:20px}}
#info-box h4{{color:#2874a6;margin:16px 0 6px 0;font-size:14px}}
#info-box p{{font-size:13px;line-height:1.6;margin:6px 0;color:#333}}
#info-box ul{{font-size:13px;line-height:1.6;margin:6px 0 6px 20px;color:#333}}
#info-close{{float:right;cursor:pointer;font-size:22px;color:#999;border:none;
  background:none;padding:0 4px;line-height:1}}
#info-close:hover{{color:#333}}
#info-dont-show{{margin:16px 0 8px 0;font-size:13px;color:#666;cursor:pointer}}
#info-dont-show input{{cursor:pointer;margin-right:6px}}
.info-link{{color:#2874a6;cursor:pointer;text-decoration:underline;font-size:13px}}
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
  // Update header sort indicators
  var ths = table.querySelectorAll('th.sort-th');
  ths.forEach(function(th) {{ th.classList.remove('sort-asc','sort-desc'); }});
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
  // Set indicator on the clicked column header
  var clickedTh = table.querySelectorAll('th')[colIdx];
  if (clickedTh) clickedTh.classList.add(asc ? 'sort-asc' : 'sort-desc');
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
// Info overlay
function showInfo() {{
  document.getElementById('info-overlay').classList.add('show');
}}
function hideInfo() {{
  document.getElementById('info-overlay').classList.remove('show');
  var cb = document.getElementById('info-dont-show-cb');
  if (cb && cb.checked) {{
    try {{ localStorage.setItem('flat_searcher_info_dismissed', '1'); }} catch(e) {{}}
  }}
}}
// Auto-show on first visit (if not dismissed before)
(function() {{
  try {{
    if (!localStorage.getItem('flat_searcher_info_dismissed')) {{
      document.addEventListener('DOMContentLoaded', function() {{
        document.getElementById('info-overlay').classList.add('show');
      }});
    }}
  }} catch(e) {{}}
}})();
</script>
</head><body>
<h2>Riga flat deals - {today} <span class="info-link" onclick="showInfo()" style="font-size:14px;font-weight:normal">About this page</span></h2>
{browser_link}
<p>Districts: {', '.join(config.DISTRICTS.keys())} &middot; Sources: ss.com, city24.lv</p>
<p class="note">Scoring: {status_note}</p>
{comparison_html}
{exceptional_html}
{body_sections}
{map_html}
<hr><p class="note">Generated by Flat_Searcher. Higher deal score = cheaper than
expected for its size/floor/district. Always verify on the source site before
contacting.</p>
{unsub}
<!-- Info overlay -->
<div id="info-overlay" onclick="if(event.target===this)hideInfo()">
<div id="info-box">
<button id="info-close" onclick="hideInfo()">&times;</button>
<h3>How this page works</h3>

<h4>What is this?</h4>
<p>This is an automated daily digest of apartment listings in three Riga
districts: <b>Zolitude</b>, <b>Sampeteris/Pleskodale</b>, and <b>Imanta</b>.
It scans two sources every day:</p>
<ul>
<li><b>ss.com</b> &mdash; Latvia's largest classifieds site (~278 listings)</li>
<li><b>city24.lv</b> &mdash; a real-estate portal (~12-29 listings)</li>
</ul>
<p>After scraping, it removes duplicates (the same flat is often on both
sites) and filters out implausible prices. Typically <b>250+ unique
listings</b> remain.</p>

<h4>Why so few listings in the tables?</h4>
<p>The tables show only the <b>top 10 best deals per type</b> (rent and
sale), ranked by deal score. Showing all 250+ would be overwhelming.
The <b>map at the bottom shows every listing</b> with coordinates &mdash;
click "map" next to any source link to jump to it.</p>

<h4>What is "Deal score"?</h4>
<p>Deal score measures how much <b>cheaper</b> a listing is compared to
what a regression model expects for its size, floor, and district.
Higher score = bigger bargain. The model trains on historical data
from prior days. When there isn't enough history yet (first few weeks),
it falls back to a simpler z-score comparison.</p>

<h4>What are the badges?</h4>
<ul>
<li><b style="color:#27ae60">NEW</b> &mdash; first time this listing appears</li>
<li><b style="color:#e67e22">PRICE DROP</b> &mdash; price dropped since last shown</li>
<li><b style="color:#2980b9">REAPPEARED</b> &mdash; seen before, back after a gap</li>
<li><b style="color:#8e44ad">SHORT-TERM/DAILY</b> &mdash; priced per day, not per month</li>
</ul>

<h4>What is the timeline under each row?</h4>
<p>It shows the price history: the original listing price, any changes
over time, and how many days the listing has been on the market.
Historical data comes from <b>CenuMednieks.lv</b> (for ss.com listings)
and our own daily tracking. "Previous ads at this address" are older
listings at the same location &mdash; they may or may not be the same flat.</p>

<h4>Top exceptional deals</h4>
<p>The ranked cards at the top combine multiple signals: deal score,
price drop percentage, days on market, and price vs area average.
Short-term/daily rentals are excluded from this ranking.</p>

<h4>How often does it update?</h4>
<p>The full digest runs <b>once daily</b>. An <b>hourly scan</b> checks
for exceptional bargains and sends an instant alert email if any listing
scores above the threshold. The hourly scan uses fewer pages to
avoid overloading the source sites.</p>

<h4>Click column headers to sort</h4>
<p>All tables are sortable. Click any header to sort ascending, click
again for descending. The timeline rows stay attached to their listing
during sorting.</p>

<label id="info-dont-show">
<input type="checkbox" id="info-dont-show-cb">
Don't show this automatically next time
</label>
</div>
</div>
</body></html>"""


def _build_map_html(markers):
    """Build an inline map at the bottom of the page with markers for each listing.

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
            "id": m.get("marker_id", ""),
            "lat": m["lat"],
            "lon": m["lon"],
            "popup": m["popup"],
            "color": color,
            "deal_type": m.get("deal_type", ""),
        })

    js_data = _json.dumps(js_markers, ensure_ascii=False)
    n_markers = len(js_markers)

    return f"""
<!-- Inline map at bottom -->
<div id="map-container">
  <div class="map-header">Map ({n_markers} listings)</div>
  <div id="map"></div>
</div>
<script>
(function() {{
  var map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  }}).addTo(map);

  var markers = {js_data};
  var markerIndex = {{}};  // id -> Leaflet circle marker
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
    if (m.id) markerIndex[m.id] = circle;
  }});

  // Fit bounds to show all markers
  if (markers.length > 0) {{
    var bounds = L.latLngBounds(markers.map(function(m){{ return [m.lat, m.lon]; }}));
    map.fitBounds(bounds, {{padding: [30, 30]}});
  }}

  // Expose for showOnMap
  window._leafletMap = map;
  window._markerIndex = markerIndex;
}})();

function showOnMap(markerId) {{
  var map = window._leafletMap;
  var marker = window._markerIndex && window._markerIndex[markerId];
  if (!map || !marker) return;
  var ll = marker.getLatLng();
  map.setView(ll, 16);
  marker.openPopup();
  document.getElementById('map-container').scrollIntoView({{
    behavior: 'smooth', block: 'start'
  }});
}}
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
def _alert_row_html(listing, score, price_data=None):
    score_str = f"{score:+.2f}" if score is not None else "-"
    # Add price history context if available
    context = ""
    if price_data is not None:
        listed_date, days_market, first_price, change_pct = _get_listing_age(listing, price_data)
        parts = []
        if days_market:
            parts.append(f"{days_market}d on market")
        if first_price:
            parts.append(f"first: {first_price}")
        if change_pct:
            parts.append(change_pct)
        if parts:
            context = (f'<tr><td colspan="8" style="padding:4px 8px;border-top:none;'
                       f'background:#f5f5f5;font-size:11px;color:#666">'
                       f'{" &middot; ".join(parts)}</td></tr>')
    return (
        "<tr>"
        f"<td style='text-align:center;font-size:20px;font-weight:bold;color:#e74c3c'>{score_str}</td>"
        f"<td>{listing.get('district','')}</td>"
        f"<td style='text-align:center'>{listing.get('rooms','')}</td>"
        f"<td style='text-align:center'>{listing.get('area_m2','')}</td>"
        f"<td style='text-align:center'>{listing.get('floor','')}</td>"
        f"<td style='text-align:right;font-weight:bold'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td><a href='{listing.get('url','')}'>{listing.get('source','')} &rarr;</a></td>"
        "</tr>"
        f"{context}"
    )


def build_alert_html(hot_deals, threshold, price_data=None):
    """hot_deals: list of (listing, score, method) tuples that exceeded threshold."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = "".join(_alert_row_html(l, s, price_data) for l, s, _ in hot_deals)
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
<h2>HOT DEAL ALERT — {n} deal(s) spotted</h2>
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


def send_alert(hot_deals, threshold, price_data=None):
    """Send an instant escalation alert email. Returns (sent:bool, info:str)."""
    if not hot_deals:
        return False, "no hot deals to alert"

    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("EMAIL_FROM")
    recipient = os.environ.get("EMAIL_TO") or ""

    html = build_alert_html(hot_deals, threshold, price_data)
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
