# -*- coding: utf-8 -*-
"""
Daily flat digest builder — produces the HTML page for docs/index.html.

Builds the HTML digest with:
  - a "vs yesterday" comparison header
  - map, walking-distance, auctions and newest-listings sections
  - main deal tables (NEW / PRICE_DROP / REAPPEARED badges)
  - a greyed "Still active from yesterday" section per deal type
and saves it to data/digests/digest_YYYY-MM-DD.html; website.build() then
publishes it as docs/index.html + docs/archive/.
"""
import os
import json
from datetime import date, datetime, timezone
from html import escape as _esc

import config
import price_history
import utils
import web_style
from utils import safe_url

try:
    from zoneinfo import ZoneInfo
    _RIGA_TZ = ZoneInfo("Europe/Riga")  # needs tzdata pkg on Windows
except Exception:
    _RIGA_TZ = None


def _now_header_str():
    """Digest header timestamp: date + time in Riga (falls back to UTC)."""
    if _RIGA_TZ:
        return datetime.now(_RIGA_TZ).strftime("%Y-%m-%d %H:%M") + " (Riga time)"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " (UTC)"


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
    "NEW": web_style.badge("NEW", "b-new"),
    "PRICE_DROP": web_style.badge("PRICE DROP", "b-down"),
    "REAPPEARED": web_style.badge("REAPPEARED", "b-reg"),
    "SHORT_TERM": web_style.badge("SHORT-TERM/DAILY", "b-auction"),
}


def _badge_html(badge, detail):
    b = _BADGE_HTML.get(badge, badge or "")
    if detail:
        b += f" <span style='color:var(--faint);font-size:11px'>({detail})</span>"
    return b


# ---------------------------------------------------------------------------
# table builders
# ---------------------------------------------------------------------------
def _change_color(change_pct):
    """Return CSS color for a change percentage string. Grey for zero."""
    if not change_pct:
        return 'var(--muted)'
    # Parse the numeric value to distinguish real changes from +0.0%
    import re as _re
    m = _re.search(r'([+-]?\d+\.?\d*)', change_pct)
    if m:
        val = float(m.group(1))
        if abs(val) < 0.05:
            return 'var(--muted)'  # grey for zero
        if val < 0:
            return 'var(--good)'  # green = price dropped
        return 'var(--bad)'  # red = price increased
    return 'var(--muted)'


def _change_sort_val(change_pct):
    """Extract numeric sort value from change percentage string."""
    if not change_pct:
        return 0.0
    import re as _re
    m = _re.search(r'([+-]?\d+\.?\d*)', change_pct)
    return float(m.group(1)) if m else 0.0


# Column layout (12 columns; Distance added for school proximity):
#  0 District  1 Distance  2 Rooms  3 m²  4 Floor  5 Price  6 EUR/m²
#  7 Deal score  8 Status  9 Listed (days)  10 First / change  11 Source
NUM_COLS = 12


def _fmt_distance(listing, digits=1):
    """'0.8 km' for sale listings with coords, '-' otherwise. A leading '~'
    marks street-level geocodes (house number unresolved) as approximate."""
    km = listing.get("_school_km")
    if km is None or listing.get("deal_type") != "sale":
        return "-"
    approx = "~" if listing.get("geo_precision") == "street" else ""
    return f"{approx}{km:.{digits}f} km"


def _t(v):
    """Escape scraped text for HTML (None -> '')."""
    return utils.esc(v)


def _source_link(listing, extra=""):
    """<a href=...>source</a> with an https-only allow-list on the href.
    Cross-source duplicates append a grey '(also on X)' hint."""
    url = safe_url(listing.get("url"))
    src = _t(listing.get("source", ""))
    also = listing.get("also_on") or []
    also_html = ""
    if also:
        names = ", ".join(
            (o.get("source") if isinstance(o, dict) else str(o)) or "?"
            for o in also)
        also_html = (f" <span class='badge b-src' "
                     f"title='Same flat also listed on {_t(names)}'>"
                     f"also on {_t(names)}</span>")
    ch = listing.get("also_cheaper")
    if ch and ch.get("price") and listing.get("price_eur"):
        diff = listing["price_eur"] - ch["price"]
        if diff > 0:
            ch_url = safe_url(ch.get("url"))
            tag = "a" if ch_url else "span"
            href = f" href='{_t(ch_url)}' rel='noopener noreferrer'" \
                if ch_url else ""
            also_html += (
                f" <{tag}{href} class='badge b-cheap' "
                f"title='Same flat listed for {_t(_fmt_price(ch['price']))} "
                f"on {_t(str(ch.get('source') or '?'))}'>"
                f"−{_fmt_price(diff)} on "
                f"{_t(str(ch.get('source') or '?'))}</{tag}>")
    if not url:
        return f"{src}{extra}{also_html}"
    return (f"<a href='{_t(url)}' rel='noopener noreferrer'>{src}</a>"
            f"{extra}{also_html}")


def _motivated_chips(listing, price_data):
    """'−€X' drop chip + amber MOTIVATED pill when the listing's
    price_history shows a real cut plus staleness or repeated cutting
    behaviour (CenuMednieks history + our own observations)."""
    if not price_data:
        return ""
    key = f"{listing.get('source')}:{listing.get('id')}"
    info = utils.flat_motivated(price_data.get(key))
    if not info or not info.get("drop_eur"):
        return ""
    bits = [f"<span class='badge b-cheap' "
            f"title='Asking price cut since first listing'>"
            f"−{_fmt_price(info['drop_eur'])}</span>"]
    if utils.is_motivated(info, config.MOTIVATED_STALE_DAYS_FLAT,
                          config.MOTIVATED_MIN_DROP_EUR_FLAT):
        why = []
        if info.get("days", 0) >= config.MOTIVATED_STALE_DAYS_FLAT:
            why.append(f"{info['days']} days on market")
        if info.get("trail_drops", 0) >= config.MOTIVATED_MIN_TRAIL_DROPS:
            why.append(f"{info['trail_drops']} cuts observed")
        if info.get("relists", 0) >= config.MOTIVATED_MIN_RELISTINGS:
            why.append(f"{info['relists']} relistings")
        bits.append(f"<span class='badge b-mot' "
                    f"title='Seller may be negotiable: "
                    f"{_t('; '.join(why))}'>MOTIVATED</span>")
    return " " + " ".join(bits)


def _main_row_html(item, price_data=None, row_idx=0):
    listing, score, method, badge, detail = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    zebra = ' class="z"' if row_idx % 2 else ''
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr class="timeline-row"{zebra}><td colspan="{NUM_COLS}" '
                        f'style="padding:6px 10px;border-top:none;'
                        f'border-bottom:1px solid var(--line);background:var(--row-alt);'
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
    dist_str = _fmt_distance(listing)
    dist_sort = listing.get("_school_km") if listing.get("_school_km") is not None else 9999

    # "map" link — only if the listing has coordinates
    map_link = ""
    if listing.get('lat') and listing.get('lon'):
        marker_id = f"{listing.get('source','')}:{listing.get('id','')}"
        map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                    f"return false\" style=\"font-size:11px;color:var(--link)\">map</a>")

    return (
        f"<tr{zebra}>"
        f"<td>{_t(listing.get('district',''))}</td>"
        f"<td style='text-align:right;font-size:12px' data-sort='{dist_sort}'>{dist_str}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('rooms',0) or 0}'>{_t(listing.get('rooms',''))}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('area_m2',0) or 0}'>{_t(listing.get('area_m2',''))}</td>"
        f"<td style='text-align:right'>{_t(listing.get('floor',''))}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:right;font-size:16px;font-weight:bold;color:var(--accent)' data-sort='{score_val}'>{score_str}</td>"
        f"<td>{_badge_html(badge, detail)}</td>"
        f"<td style='text-align:right;font-size:12px;color:var(--muted)' data-sort='{listed_date}'>{listed_days}</td>"
        f"<td style='text-align:right;font-size:12px;color:{ch_color}' data-sort='{ch_sort}'>{first_change}</td>"
        f"<td>{_watch_star(listing)}{_source_link(listing, _motivated_chips(listing, price_data) + map_link)}</td>"
        "</tr>"
        f"{timeline_row}"
    )


def _still_row_html(item, price_data=None, row_idx=0):
    listing, score, method = item
    score_str = f"{score:+.2f}" if score is not None else "-"
    timeline_html = ""
    if price_data is not None:
        timeline_html = price_history.format_price_timeline_html(listing, price_data)
    zebra = ' class="z"' if row_idx % 2 else ''
    timeline_row = ""
    if timeline_html:
        timeline_row = (f'<tr class="timeline-row"{zebra}><td colspan="{NUM_COLS}" '
                        f'style="padding:6px 10px;border-top:none;'
                        f'border-bottom:1px solid var(--line);background:var(--row-alt);'
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
    dist_str = _fmt_distance(listing)
    dist_sort = listing.get("_school_km") if listing.get("_school_km") is not None else 9999

    # "map" link — only if the listing has coordinates
    map_link = ""
    if listing.get('lat') and listing.get('lon'):
        marker_id = f"{listing.get('source','')}:{listing.get('id','')}"
        map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                    f"return false\" style=\"font-size:11px;color:var(--link)\">map</a>")

    return (
        f"<tr{zebra}>"
        f"<td>{_t(listing.get('district',''))}</td>"
        f"<td style='text-align:right;font-size:12px' data-sort='{dist_sort}'>{dist_str}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('rooms',0) or 0}'>{_t(listing.get('rooms',''))}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('area_m2',0) or 0}'>{_t(listing.get('area_m2',''))}</td>"
        f"<td style='text-align:right'>{_t(listing.get('floor',''))}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:right;font-size:16px;font-weight:bold;color:var(--accent)' data-sort='{score_val}'>{score_str}</td>"
        f"<td style='text-align:right;font-size:12px;color:var(--muted)' data-sort='{listed_date}'>{listed_days}</td>"
        f"<td style='text-align:right;font-size:12px;color:{ch_color}' data-sort='{ch_sort}'>{first_change}</td>"
        f"<td>{_watch_star(listing)}{_source_link(listing, _motivated_chips(listing, price_data) + map_link)}</td>"
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
        f"<th style='text-align:right'{sort_attr.format(col=1)}>Distance</th>"
        f"<th{sort_attr.format(col=2)}>Rooms</th>"
        f"<th{sort_attr.format(col=3)}>m²</th>"
        f"<th{sort_attr.format(col=4)}>Floor</th>"
        f"<th style='text-align:right'{sort_attr.format(col=5)}>Price</th>"
        f"<th style='text-align:right'{sort_attr.format(col=6)}>EUR/m²</th>"
        f"<th style='text-align:right'{sort_attr.format(col=7)}>Deal score</th>"
    )
    if has_status:
        cols += f"<th{sort_attr.format(col=8)}>Status</th>"
        cols += (f"<th style='text-align:right'{sort_attr.format(col=9)}>Listed</th>"
                 f"<th style='text-align:right'{sort_attr.format(col=10)}>First / change</th>"
                 f"<th>Source</th>")
    else:
        cols += (f"<th style='text-align:right'{sort_attr.format(col=8)}>Listed</th>"
                 f"<th style='text-align:right'{sort_attr.format(col=9)}>First / change</th>"
                 f"<th>Source</th>")
    return (
        f"<table id='{sortable_id}' data-sortable='1'>"
        "<tr>" + cols + "</tr>"
    )


def _main_section_html(title, items, subtitle, price_data=None, table_id=""):
    if not items:
        return (f"<div class='card'><h3>{title}</h3>"
                f"<p class='note'>{subtitle}</p>"
                "<p>No new or changed qualifying deals today.</p></div>")
    rows = "".join(_main_row_html(it, price_data, idx) for idx, it in enumerate(items))
    return (
        f"<div class='card'><h3>{title}</h3>"
        f"<p class='note'>{subtitle}</p>"
        f"<div class='scroll-x'>{_table_header(table_id, has_status=True)}{rows}</table></div></div>"
    )


def _still_active_section_html(deal_type, items, price_data=None, table_id=""):
    if not items:
        return ""
    rows = "".join(_still_row_html(it, price_data, idx) for idx, it in enumerate(items))
    return (
        "<div class='card' style='opacity:0.9'>"
        f"<h3 style='color:var(--faint)'>Still active from yesterday — {deal_type}</h3>"
        "<p class='note'>These deals were in yesterday's "
        "digest and are still among the best today. No action needed unless "
        "you missed them.</p>"
        f"<div class='scroll-x'>{_table_header(table_id, has_status=False)}{rows}</table></div></div>"
    )


# ---------------------------------------------------------------------------
# "Newest listings today" section
# ---------------------------------------------------------------------------
def build_newest_html(main_deals, price_data=None, top_n=10):
    """Build a compact section showing the newest listings (NEW badge only),
    ranked by deal score (best first).

    This replaces the old "exceptional deals" composite score, which was
    misleading — it rewarded flats that were statistically cheap but
    couldn't distinguish a genuine bargain from a trash flat nobody wants.
    Newest listings are actionable: act fast on new listings, judge
    quality yourself from the source site photos.
    """
    # Collect all NEW listings across deal types
    new_items = []
    for dt, items in main_deals.items():
        for item in items:
            listing, score, method, badge, detail = item
            if badge == "NEW":
                new_items.append(item)

    if not new_items:
        return (
            '<div class="card">'
            '<h3 style="color:var(--accent);border:none;margin:0 0 8px 0">'
            'Newest listings today</h3>'
            '<p style="color:var(--muted);font-size:12px;margin:0">'
            'No brand-new listings today. Check the tables below for '
            'price drops and still-active deals.</p>'
            '</div>'
        )

    # Sort by deal score descending (best first)
    new_items.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)
    new_items = new_items[:top_n]

    rows = []
    for idx, (listing, score, method, badge, detail) in enumerate(new_items):
        score_str = f"{score:+.2f}" if score is not None else "-"
        district = _t(listing.get('district', '?'))
        rooms = _t(listing.get('rooms', '?'))
        area = _t(listing.get('area_m2', '?'))
        floor = _t(listing.get('floor', '?'))
        price_str = _fmt_price(listing.get('price_eur'), listing.get('price_unit'))
        ppu_str = _fmt_ppu(listing.get('price_per_m2'))
        deal_type = _t(listing.get('deal_type', '?'))
        source = listing.get('source', '')

        # "map" link if coordinates available
        map_link = ""
        if listing.get('lat') and listing.get('lon'):
            marker_id = f"{source}:{listing.get('id','')}"
            map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                        f"return false\" style=\"font-size:11px;color:var(--link)\">map</a>")

        zebra = ' class="z"' if idx % 2 else ''
        rows.append(
            f'<tr{zebra}>'
            f"<td style='text-align:right;font-size:16px;font-weight:bold;color:var(--accent)'>{score_str}</td>"
            f"<td>{district}</td>"
            f"<td style='text-align:right'>{rooms}</td>"
            f"<td style='text-align:right'>{area}</td>"
            f"<td style='text-align:right'>{floor}</td>"
            f"<td style='text-align:right;font-weight:bold'>{price_str}</td>"
            f"<td style='text-align:right'>{ppu_str}</td>"
            f"<td style='text-align:right'>{deal_type}</td>"
            f"<td>{_source_link(listing, map_link)}</td>"
            '</tr>'
        )

    rows_html = "".join(rows)
    n = len(new_items)
    return (
        '<div class="card">'
        '<h3 style="color:var(--accent);border:none;margin:0 0 8px 0">'
        f'Newest listings today ({n})</h3>'
        '<p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">'
        'Brand-new listings that appeared today, ranked by deal score '
        '(higher = cheaper than expected). Act fast &mdash; new listings '
        'get taken quickly. Always check the photos and condition on the '
        'source site before contacting.</p>'
        f"<div class='scroll-x'><table>"
        "<tr>"
        "<th style='text-align:right'>Deal score</th>"
        "<th style='text-align:left'>District</th>"
        "<th>Rooms</th><th>m²</th><th>Floor</th>"
        "<th style='text-align:right'>Price</th>"
        "<th style='text-align:right'>EUR/m²</th>"
        "<th>Type</th><th>Source</th>"
        "</tr>"
        f"{rows_html}"
        "</table></div>"
        '</div>'
    )


# ---------------------------------------------------------------------------
# "Walking distance to school" section
# ---------------------------------------------------------------------------
def build_near_school_html(all_listings):
    """Every in-budget sale listing within NEAR_SCHOOL_RADIUS_KM of the
    school, sorted by distance (closest first).

    This section exists so that fairly-priced flats near the school are
    never hidden by the bargain ranking: a flat priced at market rate
    scores ~0 on value and can fall below the top-N cutoff even though
    it is exactly what the buyer needs (affordable, walking distance).
    """
    near = [l for l in all_listings
            if l.get("deal_type") == "sale"
            and l.get("_school_km") is not None
            and l["_school_km"] <= config.NEAR_SCHOOL_RADIUS_KM]
    near.sort(key=lambda l: l["_school_km"])

    radius = config.NEAR_SCHOOL_RADIUS_KM
    if not near:
        return (
            '<div class="card">'
            '<h3 style="color:var(--accent);border:none;margin:0 0 8px 0">'
            f'Walking distance to school (within {radius:.0f} km)</h3>'
            '<p style="color:var(--muted);font-size:12px;margin:0">'
            'No in-budget listings within walking distance right now.</p>'
            '</div>'
        )

    hidden = max(0, len(near) - config.NEAR_SCHOOL_MAX_ROWS)
    shown = near[:config.NEAR_SCHOOL_MAX_ROWS]

    rows = []
    for idx, l in enumerate(shown):
        km = l["_school_km"]
        score = l.get("_score")
        score_str = f"{score:+.2f}" if score is not None else "-"
        score_val = score if score is not None else -999
        price_val = l.get('price_eur', 0) or 0
        ppu_val = l.get('price_per_m2', 0) or 0
        source = l.get('source', '')

        map_link = ""
        if l.get('lat') and l.get('lon'):
            marker_id = f"{source}:{l.get('id','')}"
            map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                        f"return false\" style=\"font-size:11px;color:var(--link)\">map</a>")

        zebra = ' class="z"' if idx % 2 else ''
        share = ""
        if l.get("ownership_share"):
            share = (f" <span class='badge b-end' "
                     f"title='Co-ownership share, not a whole flat'>"
                     f"SHARE {_t(l['ownership_share'])}</span>")
        rows.append(
            f'<tr{zebra}>'
            f"<td style='text-align:right;font-weight:bold' data-sort='{km:.3f}'>{_fmt_distance(l, 2)}</td>"
            f"<td>{_t(l.get('district',''))}</td>"
            f"<td style='font-size:12px'>{_t(l.get('street',''))}{share}</td>"
            f"<td style='text-align:right' data-sort='{l.get('rooms',0) or 0}'>{_t(l.get('rooms',''))}</td>"
            f"<td style='text-align:right' data-sort='{l.get('area_m2',0) or 0}'>{_t(l.get('area_m2',''))}</td>"
            f"<td style='text-align:right'>{_t(l.get('floor',''))}</td>"
            f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(l.get('price_eur'), l.get('price_unit'))}</td>"
            f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(l.get('price_per_m2'))}</td>"
            f"<td style='text-align:right;font-size:16px;font-weight:bold;color:var(--accent)' data-sort='{score_val}'>{score_str}</td>"
            f"<td>{_watch_star(l)}{_source_link(l, map_link)}</td>"
            '</tr>'
        )

    rows_html = "".join(rows)
    n = len(near)
    n_approx = sum(1 for l in near if l.get("geo_precision") == "street")
    approx_note = (f' <span style="color:var(--faint);font-size:11px">'
                   f'(~ = street-level position, house number not resolved; '
                   f'{n_approx} such)</span>') if n_approx else ''
    more_note = (f' <span style="color:var(--faint);font-size:11px">'
                 f'(+{hidden} more within {radius:.0f} km)</span>') if hidden else ''
    tid = "tbl_near_school"
    return (
        '<div class="card">'
        '<h3 style="color:var(--accent);border:none;margin:0 0 8px 0">'
        f'Walking distance to school ({n} within {radius:.0f} km){more_note}{approx_note}</h3>'
        '<p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">'
        'Every in-budget listing within walking distance of '
        f'{config.SCHOOL_NAME}, closest first. A flat here is fairly priced '
        'for its size even when its deal score is near zero &mdash; the '
        'bargain ranking above the top-N cutoff does not apply to this '
        'section. Click column headers to sort.</p>'
        f"<div class='scroll-x'><table id='{tid}' "
        f"data-sortable='1'>"
        f"<tr>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',0)\">Distance</th>"
        f"<th style='text-align:left'>District</th>"
        f"<th style='text-align:left'>Street</th>"
        f"<th class='sort-th' onclick=\"sortTable('{tid}',3)\">Rooms</th>"
        f"<th class='sort-th' onclick=\"sortTable('{tid}',4)\">m²</th>"
        f"<th>Floor</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',6)\">Price</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',7)\">EUR/m²</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',8)\">Deal score</th>"
        f"<th>Source</th>"
        f"</tr>"
        f"{rows_html}"
        f"</table></div>"
        f'</div>'
    )


# ---------------------------------------------------------------------------
# "Disappeared — likely sold/removed" section (gone.py snapshot diff)
# ---------------------------------------------------------------------------
def build_gone_html(gone, price_data=None, today=None):
    """Yesterday's flat ads that are no longer listed.

    Each row: district, street, last asking price, days it was listed
    (price_history first_seen -> today), link. '' when nothing vanished.
    """
    if not gone:
        return ""
    price_data = price_data or {}
    try:
        today_d = date.fromisoformat(str(today)) if today else date.today()
    except ValueError:
        today_d = date.today()

    rows = []
    for i, r in enumerate(gone):
        zebra = ' class="z"' if i % 2 else ''
        entry = price_data.get(r.get("k")) or {}
        days = utils.days_since(entry.get("first_seen"), today_d)
        listed = f"{days} d" if days is not None else "?"
        url = safe_url(r.get("u"))
        link = (f'<a href="{_t(url)}" target="_blank" '
                f'rel="noopener noreferrer">view</a>') if url else "-"
        src_raw = r.get("k", "").split(":", 1)[0]
        src = _t(src_raw)
        if src_raw == "izsoles.ta.gov.lv":
            src += " <span style='color:var(--auction)'>· auction</span>"
        rows.append(
            f'<tr{zebra}>'
            f"<td style='font-size:13px'>{_t(r.get('d') or '')}</td>"
            f"<td style='font-size:13px'>{_t(r.get('s') or '—')}</td>"
            f"<td style='text-align:right;font-weight:bold'>"
            f"{_fmt_price(r.get('p')) if r.get('p') else '-'}</td>"
            f"<td style='text-align:right;color:var(--muted)'>{listed}</td>"
            f"<td style='font-size:12px;color:var(--faint)'>{src}</td>"
            f"<td style='font-size:12px'>{link}</td>"
            f'</tr>')

    n = len(gone)
    return (
        '<div class="card amber">'
        '<h3 style="color:var(--warn);border:none;margin:0 0 8px 0">'
        f'Disappeared — likely sold/removed ({n})</h3>'
        '<p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">'
        'Ads that were live yesterday but are gone from today\'s scan — '
        'usually sold (or withdrawn). "Listed" is how long our tracker '
        'saw the ad for.</p>'
        "<div class='scroll-x'><table>"
        "<tr>"
        "<th style='text-align:left'>District</th>"
        "<th style='text-align:left'>Street</th>"
        "<th style='text-align:right'>Last ask</th>"
        "<th style='text-align:right'>Listed</th>"
        "<th>Source</th><th>Link</th></tr>"
        f"{''.join(rows)}</table></div>"
        '</div>'
    )


# ---------------------------------------------------------------------------
# "State & bailiff auctions" section (izsoles.ta.gov.lv)
# ---------------------------------------------------------------------------
def build_auctions_html(auctions, top_n=15, failed=False, prev_bids=None):
    """State/bailiff auction listings from izsoles.ta.gov.lv.

    Kept separate from the main deal ranking on purpose: auctions have a
    different purchase process (registration, deposit, bidding) and prices
    that are not comparable to regular listings. Sorted by distance to
    the school, closest first. `failed` marks a broken scan so it renders
    a warning instead of a misleading "no auctions" box.
    `prev_bids` = {source:id -> yesterday's effective price} from
    flat_active.json: auctions absent from it get a NEW badge, and a
    price that moved gets a "was €X" delta under the current bid.
    """
    if not auctions:
        if failed:
            return (
                '<div class="card err">'
                '<h3 style="color:var(--auction);border:none;margin:0 0 8px 0">'
                'State & bailiff auctions (Riga apartments)</h3>'
                '<p style="color:var(--muted);font-size:12px;margin:0">'
                'The auction scan failed today — this section may be '
                'missing auctions that are still active. Check the run log '
                'for the error.</p>'
                '</div>'
            )
        return (
            '<div class="card">'
            '<h3 style="color:var(--auction);border:none;margin:0 0 8px 0">'
            'State & bailiff auctions (Riga apartments)</h3>'
            '<p style="color:var(--muted);font-size:12px;margin:0">'
            'No in-budget active auctions right now.</p>'
            '</div>'
        )

    today = date.today()

    def days_to_end(a):
        """Days until the auction ends (None when unparseable)."""
        try:
            return (date.fromisoformat(str(a.get("auction_end") or ""))
                    - today).days
        except ValueError:
            return None

    def days_to_reg(a):
        """Days until registration closes (None when unparseable). You
        can't bid without registering, so this is the real deadline."""
        try:
            return (date.fromisoformat(
                str(a.get("auction_register_until") or "")) - today).days
        except ValueError:
            return None

    def _live_days(a):
        """Days until the earliest actionable deadline, ignoring ones
        already passed (an ended auction isn't urgent, it's over)."""
        live = [d for d in (days_to_end(a), days_to_reg(a))
                if d is not None and d >= 0]
        return min(live) if live else None

    def sort_key(a):
        # Urgency = the earliest actionable deadline: a soon-closing
        # registration outranks a later auction end. Within the urgent
        # group, the closest deadline comes first, then distance; ended
        # auctions sink to the bottom.
        d = _live_days(a)
        urgent = d is not None and d <= config.AUCTION_ENDING_SOON_DAYS
        km = a.get("_school_km")
        return (d is None, not urgent, d if d is not None else 9999,
                km is None, km if km is not None else 0.0)

    items = sorted(auctions, key=sort_key)
    n_urgent = sum(
        1 for a in items
        if (_live_days(a) or 9999) <= config.AUCTION_ENDING_SOON_DAYS)
    hidden = max(0, len(items) - top_n)
    shown = items[:top_n]

    rows = []
    for idx, a in enumerate(shown):
        km = a.get("_school_km")
        dist = _fmt_distance(a, 2) if km is not None else "-"
        dist_sort = km if km is not None else 9999
        share_badge = ""
        if a.get("ownership_share"):
            share_badge = (
                f"<br><span class='badge b-end' "
                f"title='Only a co-ownership share of the flat is auctioned, "
                f"not the whole flat'>SHARE {_t(a['ownership_share'])}"
                f"</span>")
        source = a.get("source", "")
        # NEW / bid-movement vs yesterday's snapshot (empty prev_bids =
        # first tracked day -> NEW suppressed so the whole table isn't
        # flagged; delta = effective price moved since yesterday).
        key = f"{source}:{a.get('id', '')}"
        prev_p = prev_bids.get(key) if prev_bids else None
        new_badge = ("<br><span class='badge b-new' "
                     "title='First seen in today's scan'>NEW</span>"
                     if prev_bids and key not in prev_bids else "")
        eff_price = (a.get("auction_current_bid")
                     or a.get("auction_start_price"))
        bid_delta = ""
        if (prev_p is not None and eff_price is not None
                and eff_price != prev_p):
            arrow, cls = (("&#9650;", "delta-up") if eff_price > prev_p
                          else ("&#9660;", "delta-down"))
            bid_delta = (f"<br><span class='{cls}' style='font-size:11px'>"
                         f"{arrow} was {_fmt_price(prev_p)}</span>")
        rooms = a.get("rooms")
        rooms_disp = rooms if rooms is not None else "?"
        area = a.get("area_m2")
        area_disp = f"{area:.0f}" if area is not None else "?"
        sp = a.get("auction_start_price")
        sp_disp = _fmt_price(sp) if sp else "-"
        dep = a.get("auction_deposit")
        if dep:
            sp_disp += (f"<br><span style='font-size:11px;color:var(--muted)'>"
                        f"dep. {_fmt_price(dep)}</span>")
        cb = a.get("auction_current_bid")
        cb_disp = (_fmt_price(cb) if cb else "no bids") + bid_delta
        cb_style = "font-weight:bold" if cb else "color:var(--faint)"
        ap = a.get("auction_appraisal")
        ap_disp = _fmt_price(ap) if ap else "-"
        end = a.get("auction_end") or "-"
        days_end = days_to_end(a)
        days_reg = days_to_reg(a)
        if days_end is not None and days_end <= config.AUCTION_ENDING_SOON_DAYS:
            if days_end < 0:
                end = (f"{_t(end)}<br>"
                       f"<span class='badge b-ended'>ENDED</span>")
            else:
                label = ("ENDS TODAY" if days_end == 0
                         else f"ENDS IN {days_end}d")
                end = (f"{_t(end)}<br>"
                       f"<span class='badge b-end'>{label}</span>")
        else:
            end = _t(end)
        reg = a.get("auction_register_until")
        if reg:
            if days_reg is not None and days_reg <= config.AUCTION_ENDING_SOON_DAYS:
                rlabel = ("REG CLOSED" if days_reg < 0 else
                          "REG TODAY" if days_reg == 0
                          else f"REG IN {days_reg}d")
                end += f"<br><span class='badge b-reg'>{rlabel}</span>"
            end += (f"<br><span style='font-size:11px;color:var(--muted)'>reg. by "
                    f"{_t(reg)}</span>")

        map_link = ""
        if a.get("lat") and a.get("lon"):
            marker_id = f"{source}:{a.get('id', '')}"
            map_link = (f" <a href=\"#\" onclick=\"showOnMap('{marker_id}');"
                        f"return false\" style=\"font-size:11px;color:var(--auction)\">map</a>")

        zebra = ' class="z"' if idx % 2 else ''
        rows.append(
            f'<tr{zebra}>'
            f"<td style='text-align:right;font-weight:bold' data-sort='{dist_sort:.3f}'>{dist}</td>"
            f"<td style='font-size:12px'>{_t(a.get('title',''))}{share_badge}{new_badge}</td>"
            f"<td style='text-align:right' data-sort='{rooms if rooms is not None else 0}'>{rooms_disp}</td>"
            f"<td style='text-align:right' data-sort='{area if area is not None else 0}'>{area_disp}</td>"
            f"<td style='text-align:right' data-sort='{sp or 0}'>{sp_disp}</td>"
            f"<td style='text-align:right;{cb_style}' data-sort='{cb or 0}'>{cb_disp}</td>"
            f"<td style='text-align:right' data-sort='{ap or 0}'>{ap_disp}</td>"
            f"<td style='text-align:right;font-size:12px' "
            f"data-sort='{_t(str(a.get('auction_end') or ''))}'>{end}</td>"
            f"<td>{_source_link(a, map_link)}</td>"
            '</tr>'
        )

    rows_html = "".join(rows)
    n = len(auctions)
    more_note = (f' <span style="color:var(--faint);font-size:11px">'
                 f'(+{hidden} more)</span>') if hidden else ''
    urgent_note = (f' <b style="color:var(--bad)">· {n_urgent} ending '
                   f'&le;{config.AUCTION_ENDING_SOON_DAYS}d</b>'
                   ) if n_urgent else ''
    tid = "tbl_auctions"
    return (
        '<div class="card">'
        '<h3 style="color:var(--auction);border:none;margin:0 0 8px 0">'
        f'State & bailiff auctions — Riga apartments ({n}){urgent_note}'
        f'{more_note}</h3>'
        '<p style="color:var(--muted);font-size:12px;margin:0 0 10px 0">'
        'Forced-sale and state property auctions from '
        '<a href="https://izsoles.ta.gov.lv">izsoles.ta.gov.lv</a>. Starting '
        'prices are often well below market. The purchase process differs '
        'from a regular sale: you must register on the site, pay a deposit, '
        'and bid before the end date. Prices shown: start price and current '
        'bid. Sorted by distance to '
        f'{config.SCHOOL_NAME}. Always read the full auction terms. '
        '<b style="color:var(--bad)">SHARE</b> rows auction only a co-ownership '
        'fraction (dom&#257;jam&#257; da&#316;a) of a flat &mdash; you would '
        'own it jointly with the other co-owners, not get a whole flat. '
        '<b style="color:var(--good)">NEW</b> = first seen today; '
        '&#9650;/&#9660; <i>was &euro;X</i> under the bid = price moved '
        'since yesterday.</p>'
        f"<div class='scroll-x'><table id='{tid}' "
        f"data-sortable='1'>"
        f"<tr>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',0)\">Distance</th>"
        f"<th style='text-align:left'>Address</th>"
        f"<th class='sort-th' onclick=\"sortTable('{tid}',2)\">Rooms</th>"
        f"<th class='sort-th' onclick=\"sortTable('{tid}',3)\">m²</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',4)\">Start price</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',5)\">Current bid</th>"
        f"<th class='sort-th' style='text-align:right' onclick=\"sortTable('{tid}',6)\">Appraisal</th>"
        f"<th style='text-align:right'>Ends</th>"
        f"<th>Source</th>"
        f"</tr>"
        f"{rows_html}"
        f"</table></div>"
        f'</div>'
    )


def _watch_star(listing):
    """☆ button for the in-browser watchlist (FLAT_WATCH_JS). Label is
    'District · N r · A m²'; key is source:id (stable across days)."""
    src, lid = listing.get("source"), listing.get("id")
    if not src or lid is None:
        return ""
    url = str(listing.get("url") or "")
    if not url.startswith("https://"):
        url = ""
    bits = [str(listing.get("district") or "?")]
    if listing.get("rooms") is not None:
        bits.append(f"{listing['rooms']} r")
    if listing.get("area_m2") is not None:
        bits.append(f"{listing['area_m2']} m²")
    return (f"<button type='button' class='watch-star' "
            f"data-key='{_esc(f'{src}:{lid}', quote=True)}' "
            f"data-label='{_esc(' · '.join(bits), quote=True)}' "
            f"data-price='{_esc(str(listing.get('price_eur')), quote=True)}' "
            f"data-url='{_esc(url, quote=True)}' "
            f"title='Watch this listing'>☆</button> ")


# ---------------------------------------------------------------------------
# build the full HTML digest
# ---------------------------------------------------------------------------
# Fields embedded per listing so the browser can filter for a custom budget.
_FLAT_FIELDS = ("district", "rooms", "area_m2", "floor", "price_eur",
                "price_per_m2", "school_km", "score", "source", "url", "id",
                "street")

# Dictionary-encoded like the car embed (see car_digest._MARKET_DICT_FIELDS)
_FLAT_DICT_FIELDS = ("district", "source")


def _flat_market_data_html(all_scored, all_listings=None):
    """Embed every scored listing (not just the top-N shown in the tables)
    as JSON so the page can filter by a custom budget in the browser.

    ``extra`` carries every other in-budget listing rendered on the page
    (e.g. near-school rows without a score) so the watchlist can still
    resolve them as "still listed"; the budget tool reads only ``rows``.
    """
    def _row(listing, score):
        url = str(listing.get("url") or "")
        if not url.startswith("https://"):
            url = ""
        return [
            listing.get("district"), listing.get("rooms"),
            listing.get("area_m2"), listing.get("floor"),
            listing.get("price_eur"), listing.get("price_per_m2"),
            listing.get("_school_km"), score,
            listing.get("source"), url, listing.get("id"),
            listing.get("street"),
        ]

    rows = []
    embedded = set()
    for dt, items in (all_scored or {}).items():
        for entry in items:
            listing, score = entry[0], entry[1]
            if listing.get("price_eur") is None:
                continue
            rows.append(_row(listing, score))
            embedded.add((listing.get("source"), listing.get("id")))
    extra = []
    for listing in (all_listings or []):
        key = (listing.get("source"), listing.get("id"))
        if not all(key) or key in embedded:
            continue
        extra.append(_row(listing, None))
        embedded.add(key)
    dict_idx = {f: _FLAT_FIELDS.index(f) for f in _FLAT_DICT_FIELDS}
    dicts = {f: [] for f in _FLAT_DICT_FIELDS}
    dict_map = {f: {} for f in _FLAT_DICT_FIELDS}
    for row in rows + extra:
        for f, i in dict_idx.items():
            v = row[i]
            if v is None:
                continue
            d = dict_map[f].get(v)
            if d is None:
                d = len(dicts[f])
                dict_map[f][v] = d
                dicts[f].append(v)
            row[i] = d
    prices = [r[4] for r in rows + extra if r[4]]   # 4 = price_eur field
    payload = {
        "config": {"minPrice": config.MIN_SALE_PRICE_EUR,
                   # no fixed ceiling — the real data max is the bound
                   "maxPrice": max(prices) if prices else 0},
        "fields": list(_FLAT_FIELDS),
        "dict": dicts,
        "rows": rows,
        "extra": extra,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("</", "<\\/")
    return ('<script type="application/json" id="flat-listings-data">'
            + text + "</script>")


# In-browser budget filter for the flats digest: shows ALL scored listings
# within a custom budget (the daily tables cap at TOP_N_PER_TYPE), ranked
# by deal score. Filtering only — the regression score does not depend on
# the buyer's budget. Reuses the page's sortTable().
FLAT_BUDGET_JS = """
// Runs after the DOM is ready: the budget input lives in the body
// element, below this script in the head, so it does not exist at parse
// time. (No literal '<body>' here — website._inject_nav searches for it.)
function __flatBudgetInit() {
  var dataEl = document.getElementById('flat-listings-data');
  var input = document.getElementById('flat-budget-input');
  var statusEl = document.getElementById('flat-budget-status');
  var customView = document.getElementById('flat-custom-view');
  if (!dataEl || !input || !customView) return;
  var payload = JSON.parse(dataEl.textContent);
  var cfg = payload.config, F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  // Dictionary-encoded string columns -> real strings, before any reads.
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      (payload.rows || []).concat(payload.extra || []).forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  // Budget search covers the WHOLE embedded market: scored rows plus the
  // unscored extras (near-school/overflow listings) — every flat kept by
  // today's sanity floor, regardless of price.
  var rows = payload.rows.concat(payload.extra || []);
  var districtSel = document.getElementById('flat-filter-district');
  var roomsSel = document.getElementById('flat-filter-rooms');

  // Fill the district dropdown from today's data.
  if (districtSel) {
    var seen = {};
    rows.forEach(function (r) { if (r[idx.district]) seen[r[idx.district]] = 1; });
    Object.keys(seen).sort().forEach(function (d) {
      var o = document.createElement('option');
      o.value = d; o.textContent = d;
      districtSel.appendChild(o);
    });
  }

  function readFilters() {
    return {
      district: districtSel && districtSel.value ? districtSel.value : '',
      rooms: roomsSel && roomsSel.value ? roomsSel.value : ''
    };
  }
  function anyFilterSet(f) { return !!(f.district || f.rooms); }
  function passesFilters(r, f) {
    if (f.district && r[idx.district] !== f.district) return false;
    if (f.rooms === '5') {
      if (!(r[idx.rooms] >= 5)) return false;   // '5+' means five or more
    } else if (f.rooms && String(r[idx.rooms]) !== f.rooms) return false;
    return true;
  }

  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(v).toLocaleString('en-US');
  }

  function cell(text, sortVal, alignRight) {
    var td = document.createElement('td');
    td.style.padding = '5px';
    if (alignRight) td.style.textAlign = 'right';
    if (sortVal !== undefined && sortVal !== null) {
      td.setAttribute('data-sort', sortVal);
    }
    td.textContent = text;
    return td;
  }

  function render(matches, maxPrice) {
    customView.innerHTML = '';
    var h3 = document.createElement('h3');
    h3.textContent = 'Within your €' + maxPrice.toLocaleString('en-US') +
                     ' budget — ' + matches.length + ' listing(s), ranked by deal score';
    customView.appendChild(h3);
    var note = document.createElement('p');
    note.className = 'note';
    note.textContent = 'All of today\\'s scored listings within this budget ' +
      '(the daily sections below show the newest/top-N view). ' +
      'Click column headers to sort.';
    customView.appendChild(note);
    if (!matches.length) {
      var p = document.createElement('p');
      p.textContent = 'No listings within this budget today.';
      customView.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    table.id = 'flat-custom';
    var headers = ['District', 'Street', 'Distance', 'Rooms', 'm²', 'Floor',
                   'Price', 'EUR/m²', 'Score', 'Source'];
    var hr = document.createElement('tr');
    headers.forEach(function (name, col) {
      var th = document.createElement('th');
      th.className = 'sort-th';
      if (col >= 2 && col <= 8) th.style.textAlign = 'right';
      th.textContent = name;
      th.onclick = (function (c) {
        return function () { sortTable('flat-custom', c); };
      })(col);
      hr.appendChild(th);
    });
    table.appendChild(hr);
    matches.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.appendChild(cell(r[idx.district] || '?', r[idx.district]));
      tr.appendChild(cell(r[idx.street] || '—', r[idx.street]));
      var km = r[idx.school_km];
      tr.appendChild(cell(km != null ? km.toFixed(1) + ' km' : '—',
        km != null ? km : 9999, true));
      tr.appendChild(cell(String(r[idx.rooms] == null ? '—' : r[idx.rooms]), r[idx.rooms], true));
      tr.appendChild(cell(String(r[idx.area_m2] == null ? '—' : r[idx.area_m2]), r[idx.area_m2], true));
      tr.appendChild(cell(String(r[idx.floor] == null ? '—' : r[idx.floor]), r[idx.floor], true));
      tr.appendChild(cell(fmtEur(r[idx.price_eur]), r[idx.price_eur], true));
      tr.appendChild(cell(r[idx.price_per_m2] != null
        ? Math.round(r[idx.price_per_m2]).toLocaleString('en-US') : '—',
        r[idx.price_per_m2], true));
      var score = r[idx.score];
      tr.appendChild(cell(score != null ? (+score).toFixed(2) : '—', score, true));
      var srcTd = cell(r[idx.source] || '', r[idx.source]);
      var star = document.createElement('button');
      star.type = 'button';
      star.className = 'watch-star';
      star.setAttribute('data-key', r[idx.source] + ':' + r[idx.id]);
      star.setAttribute('data-label',
        ((r[idx.street] || r[idx.district] || '?') + ' · ' +
         (r[idx.rooms] || '?') + ' r · ' +
         (r[idx.area_m2] || '?') + ' m²'));
      star.setAttribute('data-price', r[idx.price_eur]);
      star.setAttribute('data-url', r[idx.url] || '');
      star.title = 'Watch this listing';
      star.textContent = '☆';
      srcTd.textContent = '';
      srcTd.appendChild(star);
      if (r[idx.url]) {
        var a = document.createElement('a');
        a.href = r[idx.url];
        a.textContent = r[idx.source] || 'link';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        srcTd.appendChild(a);
      }
      tr.appendChild(srcTd);
      table.appendChild(tr);
    });
    customView.appendChild(table);
    if (typeof window !== 'undefined' && window.__flatWatchRefresh) {
      window.__flatWatchRefresh();
    }
  }

  function hide() {
    customView.style.display = 'none';
    customView.innerHTML = '';
    if (statusEl) statusEl.textContent = '';
  }

  var timer = null;
  function apply() {
    var raw = String(input.value || '').trim();
    var maxPrice = parseInt(raw, 10);
    var filters = readFilters();
    var filtered = anyFilterSet(filters);
    if ((!raw || isNaN(maxPrice)) && !filtered) { hide(); return; }
    if (isNaN(maxPrice)) maxPrice = cfg.maxPrice;  // filters alone
    maxPrice = Math.max(cfg.minPrice, Math.min(cfg.maxPrice, maxPrice));
    var matches = rows.filter(function (r) {
      return r[idx.price_eur] != null && r[idx.price_eur] <= maxPrice &&
             passesFilters(r, filters);
    });
    matches.sort(function (a, b) {
      var sa = a[idx.score], sb = b[idx.score];
      if (sa == null && sb == null) return 0;
      if (sa == null) return 1;
      if (sb == null) return -1;
      return sb - sa;
    });
    render(matches, maxPrice);
    customView.style.display = '';
    if (statusEl) {
      statusEl.textContent = matches.length + ' of ' + rows.length +
        ' listings within €' + maxPrice.toLocaleString('en-US');
    }
  }

  input.addEventListener('input', function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 150);
  });
  [districtSel, roomsSel].forEach(function (el) {
    if (el) el.addEventListener('change', function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(apply, 150);
    });
  });
  var resetBtn = document.getElementById('flat-budget-reset');
  if (resetBtn) resetBtn.addEventListener('click', function () {
    input.value = '';
    [districtSel, roomsSel].forEach(function (el) {
      if (el) el.value = '';
    });
    hide();
  });
  var okBtn = document.getElementById('flat-budget-ok');
  if (okBtn) okBtn.addEventListener('click', function () {
    if (timer) clearTimeout(timer);
    apply();
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      if (timer) clearTimeout(timer);
      apply();
    }
  });
  var qs = (typeof location !== 'undefined' && location.search)
    ? location.search : '';
  var params = new URLSearchParams(qs);
  var urlMax = params.get('max');
  if (urlMax && !isNaN(parseInt(urlMax, 10))) input.value = urlMax;
  var urlActive = !!urlMax;
  ['district', 'rooms'].forEach(function (name) {
    var el = document.getElementById('flat-filter-' + name);
    var v = params.get(name);
    if (v && el) { el.value = v; urlActive = true; }
  });
  if (urlActive) apply();
  if (typeof window !== 'undefined') {
    window.__flatBudget = { apply: apply };
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __flatBudgetInit);
} else {
  __flatBudgetInit();
}
"""


# Watchlist: ☆/★ buttons on listing rows persist picks in localStorage
# (key watch_flats_v1). A watched flat missing from today's embedded data
# is flagged "no longer listed" (sold or ad expired). Same mechanism as
# CAR_WATCH_JS in car_digest.py.
FLAT_WATCH_JS = """
function __flatWatchInit() {
  var dataEl = document.getElementById('flat-listings-data');
  var box = document.getElementById('flat-watch-box');
  var listEl = document.getElementById('flat-watch-list');
  var countEl = document.getElementById('flat-watch-count');
  if (!dataEl || !box || !listEl) return;
  if (typeof localStorage === 'undefined') return;
  var payload = JSON.parse(dataEl.textContent);
  var F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      (payload.rows || []).concat(payload.extra || []).forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  var byKey = {};
  payload.rows.forEach(function (r) {
    if (r[idx.source] != null && r[idx.id] != null)
      byKey[r[idx.source] + ':' + r[idx.id]] = r;
  });
  (payload.extra || []).forEach(function (r) {
    if (r[idx.source] != null && r[idx.id] != null) {
      var k = r[idx.source] + ':' + r[idx.id];
      if (!byKey[k]) byKey[k] = r;
    }
  });
  var KEY = 'watch_flats_v1';
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(w) {
    try { localStorage.setItem(KEY, JSON.stringify(w)); } catch (e) {}
  }
  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(Number(v)).toLocaleString('en-US');
  }

  function refreshStars() {
    if (!document.querySelectorAll) return;
    var w = load();
    Array.prototype.forEach.call(
      document.querySelectorAll('.watch-star'), function (s) {
        s.textContent = w[s.getAttribute('data-key')] ? '★' : '☆';
      });
  }

  function toggle(btn) {
    var key = btn.getAttribute('data-key');
    if (!key) return;
    var w = load();
    if (w[key]) {
      delete w[key];
    } else {
      w[key] = {
        added: new Date().toISOString().slice(0, 10),
        label: btn.getAttribute('data-label') || key,
        price: parseFloat(btn.getAttribute('data-price')),
        url: btn.getAttribute('data-url') || ''
      };
    }
    save(w);
    refreshStars();
    renderBox();
  }

  function remove(key) {
    var w = load();
    delete w[key];
    save(w);
    refreshStars();
    renderBox();
  }

  function renderBox() {
    var w = load();
    var keys = Object.keys(w).sort(function (a, b) {
      return String(w[b].added || '').localeCompare(String(w[a].added || ''));
    });
    if (countEl) countEl.textContent = '(' + keys.length + ')';
    listEl.innerHTML = '';
    if (!keys.length) {
      var p = document.createElement('p');
      p.className = 'note';
      p.textContent = 'Nothing starred yet — click ☆ on a listing to pin it here.';
      listEl.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    keys.forEach(function (key) {
      var w0 = w[key];
      var cur = byKey[key];
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.style.padding = '6px';
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'watch-remove';
      rm.setAttribute('data-key', key);
      rm.title = 'Stop watching';
      rm.textContent = '✕';
      rm.style.cssText = 'border:0;background:none;color:var(--bad);cursor:pointer;margin-right:6px';
      td.appendChild(rm);
      var label = w0.label || key;
      var url = (cur && cur[idx.url]) ? cur[idx.url] : (w0.url || '');
      if (url) {
        var a = document.createElement('a');
        a.href = url;
        a.textContent = label;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        td.appendChild(a);
      } else {
        td.appendChild(document.createTextNode(label));
      }
      var meta = document.createElement('span');
      meta.style.fontSize = '12px';
      var delta = null;
      if (cur) {
        meta.style.color = 'var(--muted)';
        meta.textContent = ' — ' + fmtEur(cur[idx.price_eur]) +
          ' · still listed today';
        var p0 = Number(w0.price), p1 = Number(cur[idx.price_eur]);
        if (isFinite(p0) && isFinite(p1) && Math.abs(p1 - p0) >= 1) {
          delta = document.createElement('span');
          delta.style.color = p1 < p0 ? 'var(--good)' : 'var(--bad)';
          delta.style.fontWeight = 'bold';
          delta.style.fontSize = '12px';
          delta.textContent = ' ' + (p1 < p0 ? '▼' : '▲') + ' ' +
            fmtEur(Math.abs(p1 - p0)) + ' since starred';
        }
      } else {
        meta.style.color = 'var(--bad)';
        meta.textContent = ' — last seen ' + fmtEur(w0.price) +
          ' · NO LONGER LISTED (sold or expired)';
      }
      td.appendChild(meta);
      if (delta) td.appendChild(delta);
      var since = document.createElement('span');
      since.style.color = 'var(--faint)';
      since.style.fontSize = '11px';
      since.textContent = ' · watching since ' + (w0.added || '?');
      td.appendChild(since);
      tr.appendChild(td);
      table.appendChild(tr);
    });
    listEl.appendChild(table);
  }

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.getAttribute || !t.classList) return;
    if (t.classList.contains('watch-star')) { toggle(t); }
    else if (t.classList.contains('watch-remove')) {
      remove(t.getAttribute('data-key'));
    }
  });
  refreshStars();
  renderBox();
  if (typeof window !== 'undefined') {
    window.__flatWatch = { toggle: toggle, renderBox: renderBox, load: load };
    window.__flatWatchRefresh = refreshStars;
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __flatWatchInit);
} else {
  __flatWatchInit();
}
"""


def build_price_cuts_html(all_listings, price_data, top_n=None):
    """'Biggest price cuts' card — flats whose asking price dropped the most
    since they were first listed (CenuMednieks history or our own trail).
    Runs on the full scanned pool so cuts on non-top-N flats are visible."""
    if not all_listings or not price_data:
        return ""
    top_n = top_n or config.MOTIVATED_CUTS_TOP_N
    seen_keys = set()
    cuts = []
    for l in all_listings:
        key = f"{l.get('source')}:{l.get('id')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        info = utils.flat_motivated(price_data.get(key))
        if info and info.get("drop_eur", 0) >= config.MOTIVATED_MIN_DROP_EUR_FLAT:
            cuts.append((l, info))
    if not cuts:
        return ""
    cuts.sort(key=lambda x: -x[1]["drop_eur"])
    cuts = cuts[:top_n]
    rows = []
    for idx, (l, info) in enumerate(cuts):
        zebra = ' class="z"' if idx % 2 else ''
        mot = (utils.is_motivated(info, config.MOTIVATED_STALE_DAYS_FLAT,
                                  config.MOTIVATED_MIN_DROP_EUR_FLAT)
               and " <span class='badge b-mot' "
                   "title='Stale listing + real cut: seller may be "
                   "negotiable'>MOTIVATED</span>" or "")
        days = f"{info['days']}d" if info.get("days") else "?"
        url = utils.safe_url(l.get('url', ''))
        title = _t(l.get('street') or l.get('title') or l.get('district') or '?')
        title_cell = f"<a href='{url}'>{title}</a>" if url else title
        rows.append(
            f"<tr{zebra}>"
            f"<td>{_t(l.get('district',''))}</td>"
            f"<td>{title_cell}</td>"
            f"<td style='text-align:right' data-sort='{info['drop_eur']:.0f}'>"
            f"<span style='color:var(--muted)'>{_fmt_price(info['was'])}</span>"
            f" → <b>{_fmt_price(info['now'])}</b></td>"
            f"<td style='text-align:right;color:var(--good);font-weight:bold' "
            f"data-sort='{info['drop_pct']:.1f}'>−{_fmt_price(info['drop_eur'])} "
            f"(−{info['drop_pct']:.0f}%)</td>"
            f"<td style='text-align:right' data-sort='{info.get('days',0)}'>"
            f"{days}</td>"
            f"<td>{_source_link(l)}{mot}</td>"
            "</tr>")
    return (
        "<div class='card'>"
        "<h3 style='color:var(--accent);border:none;margin:0 0 4px 0'>"
        "Biggest price cuts</h3>"
        "<p style='color:var(--muted);font-size:12px;margin:0 0 8px 0'>"
        "Flats that cut their asking price since first listing — a "
        "<span class='badge b-mot'>MOTIVATED</span> seller is likely "
        "negotiable (stale listing or repeated cuts).</p>"
        "<div class='scroll-x'><table id='tbl_cuts' data-sortable='1'>"
        "<thead><tr>"
        "<th class='sort-th'>District</th><th class='sort-th'>Listing</th>"
        "<th class='sort-th'>Was → Now</th><th class='sort-th'>Cut</th>"
        "<th class='sort-th'>On market</th><th>Source</th>"
        "</tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table></div></div>")


def build_html(main_deals, still_active, comparison_html, status_note,
               price_data=None, map_markers=None,
               newest_html="", near_school_html="", auctions_html="",
               all_scored=None, all_listings=None, gone_html="",
               source_counts=None, health_pairs=None, n_auctions=None,
               auctions_failed=False, n_gone=None):
    today = date.today().isoformat()
    run_time = _now_header_str()
    sections = []

    for dt in config.DEAL_TYPES:
        items = main_deals.get(dt, [])
        if dt == "sale":
            subtitle = (f"Top {len(items)} {'new / changed' if items else ''} "
                        f"{dt} deals ranked best-first. Score = 50% value "
                        f"(cheaper than expected) + 50% proximity to "
                        f"{config.SCHOOL_NAME} (see Distance column). "
                        f"Click column headers to sort.")
        else:
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

    # Custom-budget tool: embed all scored listings, filter in the browser.
    flat_market_html = (_flat_market_data_html(all_scored, all_listings)
                        if all_scored else "")
    price_cuts_html = build_price_cuts_html(all_listings, price_data)
    flat_budget_script = (f'<script id="flat-budget-js">{FLAT_BUDGET_JS}</script>'
                          if all_scored else "")
    flat_watch_script = (f'<script id="flat-watch-js">{FLAT_WATCH_JS}</script>'
                         if all_scored else "")
    flat_budget_html = ""
    if all_scored:
        flat_budget_html = (
            "<div class='info'>"
            "<b>Your budget:</b> "
            f"<input type='number' id='flat-budget-input' min='{config.MIN_SALE_PRICE_EUR}' "
            "step='1000' "
            "placeholder='e.g. 60000' style='padding:6px 8px;border:1px solid "
            "#b8c4cf;border-radius:4px;font-size:14px;width:110px'> "
            "<button type='button' id='flat-budget-ok' style='padding:6px 10px;"
            "border:0;border-radius:4px;background:var(--accent);color:#fff;cursor:pointer;"
            "font-weight:bold'>OK</button> "
            "<button type='button' id='flat-budget-reset' style='padding:6px 10px;"
            "border:0;border-radius:4px;background:#e7edf2;cursor:pointer;"
            "font-weight:bold'>Reset</button> "
            "<span class='note' id='flat-budget-status'></span>"
            "<div style='margin-top:8px;font-size:14px'>"
            "<b>Filters:</b> "
            "<select id='flat-filter-district' style='padding:5px;border:1px "
            "solid #b8c4cf;border-radius:4px'><option value=''>Any district"
            "</option></select> "
            "<select id='flat-filter-rooms' style='padding:5px;border:1px "
            "solid #b8c4cf;border-radius:4px'><option value=''>Any rooms"
            "</option><option value='1'>1</option><option value='2'>2</option>"
            "<option value='3'>3</option><option value='4'>4</option>"
            "<option value='5'>5+</option></select>"
            "</div>"
            f"<p class='note' style='margin:6px 0 0'>Enter a maximum price "
            f"(from €{config.MIN_SALE_PRICE_EUR:,} up) and/or pick "
            "filters, then press <b>OK</b> (or Enter) to list every scored "
            "flat within it — filtered instantly in your browser from "
            "today's data, no rescraping (results also update as you type). "
            "Filters alone list all of today's scored flats in that "
            "district/room class. <b>Reset</b> "
            "returns to the default daily view. Shareable: append "
            "<b>?max=60000</b> or <b>?district=Zolitude&amp;rooms=2</b> "
            "to this page's URL.</p>"
            "</div>"
            "<details class='info' id='flat-watch-box'>"
            "<summary style='cursor:pointer'><b>★ Watchlist</b> "
            "<span class='note' id='flat-watch-count'></span></summary>"
            "<div id='flat-watch-list' style='margin-top:6px'></div>"
            "<p class='note' style='margin:6px 0 0'>Click ☆ on any listing "
            "to pin it here — stars are saved in this browser only "
            "(localStorage), never sent anywhere. A watched flat missing "
            "from today's scan shows <b>no longer listed</b> — sold or the "
            "ad expired (the link may still open briefly). Price moves "
            "since you starred it show as ▼/▲.</p></details>"
            "<div id='flat-custom-view' style='display:none'></div>")

    # Map section (Leaflet.js with OpenStreetMap tiles — free, no API key)
    map_html = _build_map_html(map_markers) if map_markers else ""

    # Per-source coverage line + scrape-health warning box, so a broken
    # source can't silently masquerade as "no listings today" (the car
    # digest has had this since the source-outage work).
    coverage_note = ""
    if source_counts:
        bits = " · ".join(f"{_t(s)} {_t(str(c))}"
                          for s, c in sorted(source_counts.items()))
        tail = ""
        if n_auctions is not None:
            tail = f" · izsoles {_t(str(n_auctions))} auctions"
        if auctions_failed:
            tail += " (auction scan failed)"
        coverage_note = f"<p class='note'>Today's scan: {bits}{tail}.</p>"
    health_box = ""
    if health_pairs:
        issues = "".join(
            f"<li><b>{_t(str(k))}</b>: {_t(str(m))}</li>"
            for k, m in health_pairs)
        health_box = (
            "<div class='warn'>"
            "<b>Warning: scrape health issues — today's coverage may be "
            "incomplete:</b>"
            f"<ul style='margin:6px 0;font-size:12px'>{issues}</ul></div>")

    # KPI chips — a quick dashboard row above the sections.
    n_deals = sum(len(v) for v in main_deals.values())
    kpis = [web_style.kpi("deals today", n_deals)]
    if n_auctions is not None:
        kpis.append(web_style.kpi("auctions", n_auctions))
    if n_gone:
        kpis.append(web_style.kpi("gone", n_gone, "warn"))
    if health_pairs:
        kpis.append(web_style.kpi("health issues", len(health_pairs), "bad"))
    kpi_html = f"<div class='kpis'>{''.join(kpis)}</div>"

    _STYLE = web_style.style_block()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Riga flat deals — {today}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">  <!-- no favicon file -> no 404 noise -->
{_STYLE}
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

</script>
{flat_market_html}
{flat_budget_script}
{flat_watch_script}
</head><body>
<h2>Riga flat deals - {run_time}</h2>
<p>Districts: {', '.join(config.DISTRICTS.keys())} &middot; Sources:
ss.com, city24.lv{', izsoles.ta.gov.lv (auctions)' if config.IZSOLES_ENABLED else ''}</p>
<p class="note">Scoring: {status_note}</p>
<p class="note">Sale ranking: 50% deal score + 50% walking distance to
{config.SCHOOL_NAME} (shown in the Distance column). New builds excluded.
Sales only — rentals are out of scope.</p>
{coverage_note}
{kpi_html}
{flat_budget_html}
{health_box}
{comparison_html}
{gone_html}
{price_cuts_html}
{map_html}
{near_school_html}
{auctions_html}
{newest_html}
{body_sections}
<hr><p class="note">Generated by Flat_Searcher. Higher deal score = cheaper than
expected for its size/floor/district. Always verify on the source site before
contacting.</p>
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
        is_school = m.get("deal_type") == "school"
        is_auction = m.get("series") == "Auction"
        if is_school:
            color = "#c0392b"
        elif is_auction:
            color = "#8e44ad"  # purple — state/bailiff auction
        else:
            color = "#2874a6" if m.get("deal_type") == "rent" else "#e67e22"
        js_markers.append({
            "id": m.get("marker_id", ""),
            "lat": m["lat"],
            "lon": m["lon"],
            "popup": m["popup"],
            "color": color,
            "deal_type": m.get("deal_type", ""),
            "school": is_school,
        })

    # '</' -> '<\/' so a popup string can never terminate the <script> tag
    js_data = _json.dumps(js_markers, ensure_ascii=False).replace("</", "<\\/")
    n_markers = len(js_markers)

    return f"""
<!-- Inline map at bottom -->
<div id="map-container">
  <div class="map-header">Map ({n_markers - 1} listings + school)</div>
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
    var isSchool = m.deal_type === 'school';
    var circle = L.circleMarker([m.lat, m.lon], {{
      radius: isSchool ? 12 : 8,
      fillColor: m.color,
      color: isSchool ? '#fff' : '#fff',
      weight: isSchool ? 3 : 2,
      opacity: 1,
      fillOpacity: isSchool ? 1.0 : 0.8
    }}).addTo(map);
    circle.bindPopup(m.popup);
    if (m.id) markerIndex[m.id] = circle;
    if (isSchool) {{
      circle.bindTooltip('School', {{permanent: true, direction: 'top',
        className: 'school-label'}});
    }}
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


def save_digest(main_deals, still_active, comparison_html, status_note,
                price_data=None, map_markers=None, newest_html="",
                near_school_html="", auctions_html="", all_scored=None,
                all_listings=None, gone_html="", source_counts=None,
                health_pairs=None, n_auctions=None, auctions_failed=False,
                n_gone=None):
    """Build today's digest and write it to data/digests/. Returns (path, info)."""
    html = build_html(main_deals, still_active, comparison_html, status_note,
                      price_data, map_markers, newest_html,
                      near_school_html, auctions_html, all_scored,
                      all_listings, gone_html, source_counts=source_counts,
                      health_pairs=health_pairs, n_auctions=n_auctions,
                      auctions_failed=auctions_failed, n_gone=n_gone)
    today = date.today().isoformat()
    os.makedirs(config.DIGEST_DIR, exist_ok=True)
    digest_path = os.path.join(config.DIGEST_DIR, f"digest_{today}.html")
    with open(digest_path, "w", encoding="utf-8") as f:
        f.write(html)
    info = f"digest saved to {digest_path}"
    print(f"[notifier] {info}")
    return digest_path, info
