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
import re
from datetime import date
from html import escape as _esc

import config
import price_history
import utils
import web_style
from utils import safe_url

def _now_header_str():
    """Digest header timestamp: date + time in Riga (falls back to UTC)."""
    return utils.riga_now_str()


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
    m = re.search(r'([+-]?\d+\.?\d*)', change_pct)
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
    m = re.search(r'([+-]?\d+\.?\d*)', change_pct)
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
    for o in also:
        name = (o.get("source") if isinstance(o, dict) else str(o)) or "?"
        o_url = safe_url(o.get("url")) if isinstance(o, dict) else ""
        tip = _t(f"Same flat also listed on {name}")
        if o_url:
            also_html += (f" <a href='{_t(o_url)}' class='badge b-src' "
                          f"rel='noopener noreferrer' title='{tip}'>"
                          f"also on {_t(name)}</a>")
        else:
            also_html += (f" <span class='badge b-src' title='{tip}'>"
                          f"also on {_t(name)}</span>")
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


def _map_link(listing, color="var(--link)"):
    """'map' pseudo-link that pans the Leaflet map to this listing's
    marker — only when the listing has coordinates (markers are built
    from all_listings, so every geocoded row has one)."""
    if not (listing.get('lat') and listing.get('lon')):
        return ""
    marker_id = utils.listing_key(listing)
    # json.dumps makes it a JS string literal; esc() then makes the
    # attribute HTML-safe — a raw quote in a scraped ad id would break
    # (or inject into) the onclick.
    marker_js = utils.esc(json.dumps(marker_id))
    return (f" <a href=\"#\" onclick=\"showOnMap({marker_js});"
            f"return false\" style=\"font-size:11px;color:{color}\">map</a>")


def _motivated_chips(listing, price_data):
    """'−€X' drop chip + amber MOTIVATED pill when the listing's
    price_history shows a real cut plus staleness or repeated cutting
    behaviour (CenuMednieks history + our own observations)."""
    if not price_data:
        return ""
    key = utils.listing_key(listing)
    info = utils.flat_motivated(price_data.get(key))
    if not info or not info.get("drop_eur"):
        return ""
    bits = [f"<span class='badge b-cheap' "
            f"title='Asking price cut since first listing'>"
            f"−{_fmt_price(info['drop_eur'])}</span>"]
    if utils.flat_is_motivated(info):
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
    if info.get("at_low"):
        bits.append(f"<span class='badge b-low' "
                    f"title='Cheapest point we have ever observed for "
                    f"this flat — best moment to offer'>LOWEST SEEN</span>")
    return " " + " ".join(bits)


def _relisted_chip(listing):
    """Purple 'RELISTED' chip when main flagged this ad as a repost of a
    recently-gone listing (same flat, new ad id) — the seller withdrew
    and tried again, usually after a cut."""
    r = listing.get("_relisted") or {}
    if not r.get("gone"):
        return ""
    was = f" — was {_fmt_price(r['price'])}" if r.get("price") else ""
    tip = (f"This flat was listed before (ad gone {r['gone']}) and "
           f"reposted as a new ad{was}. Reposts often hide earlier price "
           f"history; the seller may be negotiable.")
    label = "RELISTED" + (f" · was {_fmt_price(r['price'])}"
                          if r.get("price") else "")
    return f" <span class='badge b-relist' title='{_t(tip)}'>{label}</span>"


def _vs_district_chip(listing):
    """Green '−X% vs district' chip when the listing's EUR/m2 is well
    below today's median EUR/m2 for its district (annotated by main as
    _district_median_ppu). The deal score already blends many signals;
    this is the interpretable one — 'cheap for this district'."""
    med = utils.to_float(listing.get("_district_median_ppu"))
    ppu = utils.to_float(listing.get("price_per_m2"))
    if not med or not ppu:
        return ""
    pct = (ppu - med) / med * 100
    if pct <= -10:
        return (f" <span class='badge b-cheap' "
                f"title='{_fmt_ppu(ppu)} vs "
                f"{_t(str(listing.get('district') or ''))} median "
                f"{_fmt_ppu(med)}'>"
                f"{pct:.0f}% vs district</span>")
    if pct >= 20:
        return (f" <span class='badge b-mot' "
                f"title='Priced above the {_t(str(listing.get('district') or ''))} "
                f"median ({_fmt_ppu(med)}) — watch for a cut'>"
                f"+{pct:.0f}% vs district</span>")
    return ""


def _deal_row_html(listing, score, status_cell, price_data=None, row_idx=0):
    """Shared row builder for the deal tables — main and still-active
    rows are identical except the Status cell (main only)."""
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

    map_link = _map_link(listing)
    # Row anchor + '#' permalink — deep-linkable listing rows for sharing.
    key = utils.listing_key(listing)
    row_id = f' id="r-{_t(key)}"' if key else ""
    anchor = (f" <a href='#r-{_t(key)}' title='Link to this row' "
              f"style='font-size:11px;color:var(--faint)'>#</a>"
              if key else "")

    return (
        f"<tr{zebra}{row_id}>"
        f"<td>{_t(listing.get('district',''))}</td>"
        f"<td style='text-align:right;font-size:12px' data-sort='{dist_sort}'>{dist_str}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('rooms',0) or 0}'>{_t(listing.get('rooms',''))}</td>"
        f"<td style='text-align:right' data-sort='{listing.get('area_m2',0) or 0}'>{_t(listing.get('area_m2',''))}</td>"
        f"<td style='text-align:right'>{_t(listing.get('floor',''))}</td>"
        f"<td style='text-align:right' data-sort='{price_val}'>{_fmt_price(listing.get('price_eur'), listing.get('price_unit'))}</td>"
        f"<td style='text-align:right' data-sort='{ppu_val}'>{_fmt_ppu(listing.get('price_per_m2'))}</td>"
        f"<td style='text-align:right;font-size:16px;font-weight:bold;color:var(--accent)' data-sort='{score_val}'>{score_str}</td>"
        f"{status_cell}"
        f"<td style='text-align:right;font-size:12px;color:var(--muted)' data-sort='{listed_date}'>{listed_days}</td>"
        f"<td style='text-align:right;font-size:12px;color:{ch_color}' data-sort='{ch_sort}'>{first_change}</td>"
        f"<td>{_watch_star(listing)}{_source_link(listing, _motivated_chips(listing, price_data) + _relisted_chip(listing) + _vs_district_chip(listing) + map_link + anchor)}</td>"
        "</tr>"
        f"{timeline_row}"
    )


def _main_row_html(item, price_data=None, row_idx=0):
    listing, score, method, badge, detail = item
    return _deal_row_html(listing, score,
                          f"<td>{_badge_html(badge, detail)}</td>",
                          price_data, row_idx)


def _still_row_html(item, price_data=None, row_idx=0):
    listing, score, method = item
    return _deal_row_html(listing, score, "", price_data, row_idx)


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
    if price_data is None:
        return '', '', '', ''

    key = utils.listing_key(listing)
    entry = price_data.get(key, {})
    cenu = entry.get('cenumednieks')
    current_price = listing.get('price_eur')

    def _pack(date_str, days, first_price):
        """(listed, days, first, change) 4-tuple for one history source.
        Days: the source's own figure, else derived from the date.
        Change: % delta first_price -> current ask."""
        if days is None:
            try:
                days = (date.today()
                        - date.fromisoformat((date_str or '')[:10])).days
            except ValueError:
                days = None
        change_pct = ''
        if first_price and current_price and first_price > 0:
            pct = ((current_price - first_price) / first_price) * 100
            change_pct = f"{pct:+.1f}%"
        return ((date_str or '')[:10],
                str(days) if days is not None else '',
                _fmt_price(first_price) if first_price else '',
                change_pct)

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
        return _pack(listed, days, first_price)

    # Fall back to our own tracking.
    # Use first_seen (set once, never overwritten) for the date, and
    # our_tracking[0] for the first observed price.
    first_seen = entry.get('first_seen')
    our = entry.get('our_tracking', [])
    if first_seen or our:
        first_date = first_seen or (our[0].get('date', '') if our else '')
        first_price = our[0].get('price') if our else None
        return _pack(first_date, None, first_price)

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


def _hero_card_html(items, price_data=None):
    """Compact 'deal of the day' card for the #1 ranked sale listing."""
    if not items:
        return ""
    listing, score, method, badge, detail = items[0]
    key = utils.listing_key(listing)
    bits = []
    for k, fmt in (("rooms", "{} rm"), ("area_m2", "{} m²")):
        v = listing.get(k)
        if v:
            bits.append(_t(fmt.format(v)))
    dist = _fmt_distance(listing)
    if dist:
        bits.append(_t(dist))
    chips = (_motivated_chips(listing, price_data) +
             _relisted_chip(listing) + _vs_district_chip(listing))
    score_str = f"{score:+.2f}" if score is not None else "-"
    return (
        "<div class='card' style='border-left:4px solid var(--accent)'>"
        "<h3 style='margin:0 0 6px 0'>Deal of the day</h3>"
        f"<p style='margin:0;font-size:15px'>"
        f"<a href='{_t(listing.get('url',''))}' target='_blank' rel='noopener'>"
        f"{_t(listing.get('district',''))} — {_fmt_price(listing.get('price_eur'))}</a> "
        f"<span class='note'>{' · '.join(bits)}</span> "
        f"<b style='color:var(--accent)'>{score_str}</b>{chips}</p>"
        f"<p class='note' style='margin:4px 0 0'>{_t(listing.get('street','') or '')}"
        f"{' — ' + _t(detail) if detail else ''}"
        + (f" · <a href='#r-{_t(key)}'>jump to row</a>" if key else "")
        + "</p>"
        "</div>")


_HIST_BUCKET_EUR = 5000


def _histogram_card_html(all_listings):
    """Ask-price histogram over today's sale listings — a one-glance
    'where the market sits' snapshot behind the budget tool."""
    prices = sorted(p for p in
                    (utils.to_float(l.get("price_eur"))
                     for l in (all_listings or [])
                     if l.get("deal_type") == "sale")
                    if p and p > 0)
    if len(prices) < 10:
        return ""
    hi = int(prices[-1] // _HIST_BUCKET_EUR) + 1
    lo = int(prices[0] // _HIST_BUCKET_EUR)
    buckets = [0] * (hi - lo)
    for p in prices:
        buckets[int(p // _HIST_BUCKET_EUR) - lo] += 1
    peak = max(buckets)
    w, h, bh = 640, 90, 60
    bw = w / len(buckets)
    bars = []
    for i, n in enumerate(buckets):
        if not n:
            continue
        bh_i = max(2, int(n / peak * bh))
        bars.append(f"<rect x='{i*bw:.1f}' y='{bh-bh_i}' width='{bw-1:.1f}' "
                    f"height='{bh_i}' style='fill:var(--accent)' "
                    f"opacity='0.75'>"
                    f"<title>€{int((lo+i)*_HIST_BUCKET_EUR):,}–"
                    f"€{int((lo+i+1)*_HIST_BUCKET_EUR):,}: {n} ads</title>"
                    f"</rect>")
    svg = (f"<svg viewBox='0 0 {w} {h}' style='width:100%;max-width:640px;"
           f"height:auto;display:block' preserveAspectRatio='none'>"
           f"{''.join(bars)}"
           f"<text x='0' y='{h-2}' font-size='9' style='fill:var(--faint)'>"
           f"€{lo*_HIST_BUCKET_EUR:,}</text>"
           f"<text x='{w}' y='{h-2}' font-size='9' text-anchor='end' "
           f"style='fill:var(--faint)'>€{hi*_HIST_BUCKET_EUR:,}</text></svg>")
    return ("<div class='card'>"
            "<h3 style='margin:0 0 4px 0'>Ask-price spread — today "
            f"({len(prices)} sale ads)</h3>"
            "<p class='note' style='margin:0 0 6px 0'>Today's sale asking "
            f"prices in {_fmt_price(_HIST_BUCKET_EUR)} buckets — hover a bar "
            "for its range. The budget tool below slices this same pool.</p>"
            f"{svg}</div>")


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

        map_link = _map_link(listing)

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
            f"<td>{_source_link(listing, _relisted_chip(listing) + _vs_district_chip(listing) + map_link)}</td>"
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

        map_link = _map_link(l)

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
            f"<td>{_watch_star(l)}{_source_link(l, _relisted_chip(l) + _vs_district_chip(l) + map_link)}</td>"
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
        # "cut before gone" — the ask dropped before the ad vanished:
        # usually means it sold fast once the price hit the right level.
        # Show the observed trail itself so a steady slide is visible.
        cut_tag = ""
        _tp = [utils.to_float(o.get("price") or o.get("p"))
               for o in (entry.get("our_tracking") or [])]
        _tp = [p for p in _tp if p]
        if len(_tp) >= 2 and _tp[-1] < _tp[0]:
            _trail = " → ".join(_fmt_price(p) for p in _tp[-4:])
            if len(_tp) > 4:
                _trail = "… " + _trail
            cut_tag = (f"<br><span style='font-size:11px;color:var(--faint)'>"
                       f"{_trail}</span>"
                       f"<br><span style='font-size:11px;color:var(--good)'>"
                       f"−{_fmt_price(_tp[0] - _tp[-1])} before gone</span>")
        rows.append(
            f'<tr{zebra}>'
            f"<td style='font-size:13px'>{_t(r.get('d') or '')}</td>"
            f"<td style='font-size:13px'>{_t(r.get('s') or '—')}</td>"
            f"<td style='text-align:right;font-weight:bold'>"
            f"{_fmt_price(r.get('p')) if r.get('p') else '-'}{cut_tag}</td>"
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
def build_auctions_html(auctions, top_n=15, failed=False, prev_bids=None,
                        median_ppu=None, today=None):
    """State/bailiff auction listings from izsoles.ta.gov.lv.

    Kept separate from the main deal ranking on purpose: auctions have a
    different purchase process (registration, deposit, bidding) and prices
    that are not comparable to regular listings. Sorted by distance to
    the school, closest first. `failed` marks a broken scan so it renders
    a warning instead of a misleading "no auctions" box.
    `prev_bids` = {source:id -> yesterday's effective price} from
    flat_active.json: auctions absent from it get a NEW badge, and a
    price that moved gets a "was €X" delta under the current bid.
    `median_ppu` = today's city-wide median EUR/m2 across live listings —
    a "−X% vs market" chip flags auctions priced well below the live
    market (the official Appraisal column is often stale, this isn't).
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

    today = date.fromisoformat(today) if isinstance(today, str) \
        else (today or date.today())

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
        key = utils.listing_key(a)
        prev_p = prev_bids.get(key) if prev_bids else None
        new_badge = ("<br><span class='badge b-new' "
                     "title='First seen in today's scan'>NEW</span>"
                     if prev_bids and key not in prev_bids else "")
        # Market-relative signal: auction's own EUR/m2 vs today's live
        # city median. Green chip at -15% or cheaper — appraisal-based
        # columns can't answer "is this cheap TODAY" the way this can.
        vs_market = ""
        ppu = utils.to_float(a.get("price_per_m2"))
        if ppu and median_ppu:
            pct = (ppu - median_ppu) / median_ppu * 100
            if pct <= -15:
                vs_market = (
                    f"<br><span class='badge b-cheap' "
                    f"title='{_fmt_ppu(ppu)} vs today\'s city median "
                    f"{_fmt_ppu(median_ppu)}'>"
                    f"{pct:.0f}% vs market</span>")
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
        # FIRST BID: yesterday's effective price was still the start
        # price (no bids) and a real bid exists now — the lot just
        # attracted its first competition.
        first_bid = ""
        if (cb and sp and prev_p is not None and prev_p <= sp):
            first_bid = ("<br><span class='badge b-new' "
                         "title='No bids yesterday — the first bid "
                         "just landed'>FIRST BID</span>")
        cb_disp = (_fmt_price(cb) if cb else
                   "<span class='badge b-ended' "
                   "title='No bids placed yet — weak competition so far'>"
                   "no bids yet</span>") + bid_delta + first_bid
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

        map_link = _map_link(a, "var(--auction)")

        zebra = ' class="z"' if idx % 2 else ''
        rows.append(
            f'<tr{zebra}>'
            f"<td style='text-align:right;font-weight:bold' data-sort='{dist_sort:.3f}'>{dist}</td>"
            f"<td style='font-size:12px'>{_t(a.get('title',''))}{share_badge}{new_badge}{vs_market}</td>"
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
# NOTE: the budget JS resolves fields by name (idx.<field>) so order is
# free, but keep appending at the END — a mid-tuple insert silently
# corrupts any consumer that still hardcodes a position.
_FLAT_FIELDS = ("district", "rooms", "area_m2", "floor", "price_eur",
                "price_per_m2", "school_km", "score", "source", "url", "id",
                "street",
                # motivated-seller signal fields for the budget tool's
                # custom view (chips: −€X, MOTIVATED, LOWEST, RELISTED,
                # −X% vs district). Null when the signal doesn't apply.
                "_drop_eur", "_mot", "_at_low", "_relisted_price",
                "_vs_district_pct",
                # sale/rent — without it rent flats leak into the sale
                # budget view (a €600/mo ad looks like an absurd bargain).
                "deal_type",
                # coordinates + our own trail, for the map link and the
                # 'seen N d' line in the custom view (car-view parity).
                "lat", "lon", "_first_seen", "_price_hist")

# Dictionary-encoded like the car embed (see car_digest._MARKET_DICT_FIELDS)
_FLAT_DICT_FIELDS = ("district", "source", "deal_type", "_first_seen")


def _flat_market_data_html(all_scored, all_listings=None, price_data=None):
    """Embed every scored listing (not just the top-N shown in the tables)
    as JSON so the page can filter by a custom budget in the browser.

    ``extra`` carries every other in-budget listing rendered on the page
    (e.g. near-school rows without a score) so the watchlist can still
    resolve them as "still listed"; the budget tool reads only ``rows``.
    ``price_data`` feeds the motivated/relisted/vs-district signal fields
    so the custom view shows the same chips as the daily tables.
    """
    price_data = price_data or {}

    def _row(listing, score):
        url = str(listing.get("url") or "")
        if not url.startswith("https://"):
            url = ""
        key = utils.listing_key(listing)
        info = utils.flat_motivated(price_data.get(key))
        drop = mot = low = None
        if info:
            if info.get("drop_eur"):
                drop = round(info["drop_eur"])
            if utils.flat_is_motivated(info):
                mot = 1
            if info.get("at_low"):
                low = 1
        relisted = (listing.get("_relisted") or {}).get("price")
        vs = None
        med = utils.to_float(listing.get("_district_median_ppu"))
        ppu = utils.to_float(listing.get("price_per_m2"))
        if med and ppu:
            vs = round((ppu - med) / med * 100)
        entry = price_data.get(key) or {}
        hist = [[p.get("date"), p.get("price")]
                for p in (entry.get("our_tracking") or [])
                if p.get("price") is not None]
        return [
            listing.get("district"), listing.get("rooms"),
            listing.get("area_m2"), listing.get("floor"),
            listing.get("price_eur"), listing.get("price_per_m2"),
            listing.get("_school_km"), score,
            listing.get("source"), url, listing.get("id"),
            listing.get("street"),
            drop, mot, low, relisted, vs,
            listing.get("deal_type"),
            listing.get("lat"), listing.get("lon"),
            entry.get("first_seen"), hist or None,
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
    dicts = utils.dict_encode(rows + extra, _FLAT_FIELDS, _FLAT_DICT_FIELDS)
    _P = _FLAT_FIELDS.index("price_eur")
    prices = [r[_P] for r in rows + extra if r[_P]]
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


# In-browser budget filter for the flats digest: shows ALL scored
# listings within a custom budget. The ~340-line JS literal lives in
# web_style.py (home of the shared page JS: SORT_JS/watch_js).
FLAT_BUDGET_JS = web_style.FLAT_BUDGET_JS




# Watchlist: ☆/★ buttons on listing rows persist picks in localStorage
# (key watch_flats_v1). A watched flat missing from today's embedded data
# is flagged "no longer listed" (sold or ad expired). Same mechanism as
# CAR_WATCH_JS in car_digest.py.
FLAT_WATCH_JS = web_style.watch_js("flat", "flat-listings-data",
                                    "watch_flats_v1")


def build_stale_html(all_listings, price_data, top_n=None):
    """'Stale & stubborn' card — sale ads older than STALE_MIN_DAYS_FLAT
    with no recorded price cut. These are the complement of the
    price-cuts card: sellers who haven't moved yet, i.e. the pool future
    cuts (and negotiable asks) come from."""
    if not all_listings or not price_data:
        return ""
    top_n = top_n or config.STALE_TOP_N
    rows_src = []
    for l in utils.unique_by_key(all_listings):
        if l.get("deal_type") != "sale":
            continue
        key = utils.listing_key(l)
        entry = price_data.get(key) or {}
        info = utils.flat_motivated(entry)
        if info and info.get("drop_eur"):
            continue  # already cut — the cuts card owns it
        cenu = entry.get("cenumednieks") or {}
        days = cenu.get("days_on_market")
        if days is None and entry.get("first_seen"):
            days = utils.days_since(entry["first_seen"])
        if not days or days < config.STALE_MIN_DAYS_FLAT:
            continue
        rows_src.append((l, int(days)))
    if not rows_src:
        return ""
    rows_src.sort(key=lambda x: -x[1])
    rows = []
    for idx, (l, days) in enumerate(rows_src[:top_n]):
        zebra = ' class="z"' if idx % 2 else ''
        url = utils.safe_url(l.get('url', ''))
        title = _t(l.get('street') or l.get('title') or l.get('district') or '?')
        title_cell = f"<a href='{url}'>{title}</a>" if url else title
        rows.append(
            f"<tr{zebra}>"
            f"<td>{_t(l.get('district',''))}</td>"
            f"<td>{title_cell}</td>"
            f"<td style='text-align:right' data-sort='{l.get('price_eur') or 0}'>"
            f"{_fmt_price(l.get('price_eur'))}</td>"
            f"<td style='text-align:right' data-sort='{days}'>"
            f"{days} d</td>"
            f"<td>{_source_link(l)}{_map_link(l)}</td>"
            "</tr>")
    return (
        "<div class='card'>"
        "<h3 style='color:var(--warn);border:none;margin:0 0 4px 0'>"
        "Stale &amp; stubborn</h3>"
        "<p style='color:var(--muted);font-size:12px;margin:0 0 8px 0'>"
        "Sale ads older than "
        f"{config.STALE_MIN_DAYS_FLAT} days that have never cut their "
        "ask — sellers holding out. Watch these: when the cut finally "
        "comes it tends to be real.</p>"
        "<div class='scroll-x'><table id='tbl_stale' data-sortable='1'>"
        "<thead><tr>"
        "<th class='sort-th'>District</th><th class='sort-th'>Listing</th>"
        "<th class='sort-th'>Ask</th><th class='sort-th'>On market</th>"
        "<th>Source</th>"
        "</tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table></div></div>")


def build_price_cuts_html(all_listings, price_data, top_n=None):
    """'Biggest price cuts' card — flats whose asking price dropped the most
    since they were first listed (CenuMednieks history or our own trail).
    Runs on the full scanned pool so cuts on non-top-N flats are visible."""
    if not all_listings or not price_data:
        return ""
    top_n = top_n or config.MOTIVATED_CUTS_TOP_N
    cuts = []
    for l in utils.unique_by_key(all_listings):
        key = utils.listing_key(l)
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
        mot = (web_style.motivated_badge()
               if utils.flat_is_motivated(info) else "")
        days = f"{info['days']}d" if info.get("days") else "?"
        url = utils.safe_url(l.get('url', ''))
        title = _t(l.get('street') or l.get('title') or l.get('district') or '?')
        title_cell = f"<a href='{url}'>{title}</a>" if url else title
        map_link = _map_link(l)
        _ent = price_data.get(utils.listing_key(l)) or {}
        trail_pts = [(o.get("date"), o.get("price") or o.get("p"))
                     for o in (_ent.get("our_tracking") or [])]
        spark = utils.sparkline_svg(trail_pts, title="asking-price trail")
        rows.append(
            f"<tr{zebra}>"
            f"<td>{_t(l.get('district',''))}</td>"
            f"<td>{title_cell}</td>"
            f"<td style='text-align:right' data-sort='{info['drop_eur']:.0f}'>"
            f"<span style='color:var(--muted)'>{_fmt_price(info['was'])}</span>"
            f" → <b>{_fmt_price(info['now'])}</b>{spark}</td>"
            f"<td style='text-align:right;color:var(--good);font-weight:bold' "
            f"data-sort='{info['drop_pct']:.1f}'>−{_fmt_price(info['drop_eur'])} "
            f"(−{info['drop_pct']:.0f}%)</td>"
            f"<td style='text-align:right' data-sort='{info.get('days',0)}'>"
            f"{days}</td>"
            f"<td>{_source_link(l)}{mot}{map_link}</td>"
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
               *, price_data=None, map_markers=None,
               newest_html="", near_school_html="", auctions_html="",
               all_scored=None, all_listings=None, gone_html="",
               source_counts=None, health_pairs=None, n_auctions=None,
               auctions_failed=False, n_gone=None, market_pulse=None,
               today=None):
    today = today or date.today().isoformat()
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

    hero_html = _hero_card_html(main_deals.get("sale", []), price_data)
    body_sections = (hero_html + _histogram_card_html(all_listings)
                     + "".join(sections))

    # Custom-budget tool: embed all scored listings, filter in the browser.
    flat_market_html = (_flat_market_data_html(all_scored, all_listings,
                                               price_data)
                        if all_scored else "")
    price_cuts_html = build_price_cuts_html(all_listings, price_data)
    stale_html = build_stale_html(all_listings, price_data)
    flat_budget_script = (f'<script id="flat-budget-js">{FLAT_BUDGET_JS}</script>'
                          if all_scored else "")
    flat_watch_script = (f'<script id="flat-watch-js">{FLAT_WATCH_JS}</script>'
                         if all_scored else "")
    flat_budget_html = ""
    if all_scored:
        flat_budget_html = (
            "<div class='info'>"
            "<b>Your budget:</b> "
            f"<input type='number' id='flat-budget-min' min='0' "
            "step='1000' "
            f"placeholder='min €{config.MIN_SALE_PRICE_EUR:,}' "
            "style='width:90px'> &ndash; "
            f"<input type='number' id='flat-budget-input' min='{config.MIN_SALE_PRICE_EUR}' "
            "step='1000' "
            "placeholder='e.g. 60000' style='width:110px'> "
            "<button type='button' id='flat-budget-ok' "
            "class='primary'>OK</button> "
            "<button type='button' id='flat-budget-reset' "
            "class='ghost'>Reset</button> "
            "<span class='note' id='flat-budget-status'></span>"
            "<div style='margin-top:8px;font-size:14px'>"
            "<b>Filters:</b> "
            "<select id='flat-filter-district'><option value=''>Any district"
            "</option></select> "
            "<select id='flat-filter-rooms'><option value=''>Any rooms"
            "</option><option value='1'>1</option><option value='2'>2</option>"
            "<option value='3'>3</option><option value='4'>4</option>"
            "<option value='5'>5+</option></select> "
            "</div>"
            f"<p class='note' style='margin:6px 0 0'>Enter a maximum price "
            f"(from €{config.MIN_SALE_PRICE_EUR:,} up) and/or pick "
            "filters, then press <b>OK</b> (or Enter) to list every scored "
            "flat within it — filtered instantly in your browser from "
            "today's data, no rescraping (results also update as you type). "
            "Filters alone list all of today's scored flats in that "
            "district/room class. <b>Reset</b> "
            "returns to the default daily view. Shareable: append "
            "<b>?min=40000&amp;max=60000</b> or "
            "<b>?district=Zolitude&amp;rooms=2</b> "
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
    if market_pulse and market_pulse.get("ppu"):
        _d = market_pulse.get("delta")
        _dt = f" · Δ7d {_d:+.1f}%" if _d is not None else ""
        kpis.append(web_style.kpi(
            "Riga median", f"€{int(round(market_pulse['ppu'])):,}/m²{_dt}"))
        _ads = market_pulse.get("ads")
        if _ads is not None:
            _ad = market_pulse.get("ads_delta")
            _adt = f" ({_ad:+d} vs yest)" if _ad is not None else ""
            kpis.append(web_style.kpi("live ads", f"{_ads}{_adt}"))
    n_mot = 0
    new_by_district = {}
    if price_data and all_listings:
        for _l in all_listings:
            _e_ = price_data.get(utils.listing_key(_l)) or {}
            if utils.flat_is_motivated(utils.flat_motivated(_e_)):
                n_mot += 1
            if _e_.get("first_seen") == today:
                _d_ = str(_l.get("district") or "?")
                new_by_district[_d_] = new_by_district.get(_d_, 0) + 1
    if n_mot:
        kpis.append(web_style.kpi("motivated", n_mot, "good"))
    if new_by_district:
        _tip = " · ".join(f"{_t(d)} {n}"
                          for d, n in sorted(new_by_district.items(),
                                             key=lambda kv: -kv[1]))
        kpis.append(web_style.kpi(
            "new today", sum(new_by_district.values()), "good", _tip))
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
{web_style.SORT_JS}
</script>
{flat_market_html}
{flat_budget_script}
{flat_watch_script}
</head><body>
{web_style.THEME_TOGGLE_HTML}
{web_style.TOP_BTN_HTML}
<h2>Riga flat deals - {run_time}</h2>
<p>Districts: {', '.join(config.DISTRICTS.keys())} &middot; Sources:
ss.com, city24.lv{', izsoles.ta.gov.lv (auctions)' if config.IZSOLES_ENABLED else ''}</p>
<p class="note">Scoring: {status_note}</p>
<p class="note">Sale ranking: 50% deal score + 50% walking distance to
{config.SCHOOL_NAME} (shown in the Distance column). Sales only —
rentals are out of scope. New builds excluded.</p>
{coverage_note}
{kpi_html}
{flat_budget_html}
{health_box}
{comparison_html}
<p class="secnav">Jump to:
<a href="#sec-deals">Deals</a><a href="#sec-newest">Newest</a><a
href="#sec-school">Near school</a><a href="#sec-auctions">Auctions</a><a
href="#sec-cuts">Price cuts</a><a href="#sec-stale">Stale</a><a
href="#sec-gone">Gone</a><a
href="#sec-map">Map</a></p>
<div id="sec-deals">{body_sections}</div>
<div id="sec-newest">{newest_html}</div>
<div id="sec-school">{near_school_html}</div>
<div id="sec-auctions">{auctions_html}</div>
<div id="sec-cuts">{price_cuts_html}</div>
<div id="sec-stale">{stale_html}</div>
<div id="sec-gone">{gone_html}</div>
<div id="sec-map">{map_html}</div>
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
    js_data = json.dumps(js_markers, ensure_ascii=False).replace("</", "<\\/")
    n_school = sum(1 for m in js_markers if m["school"])
    n_list = len(js_markers) - n_school
    map_title = (f"Map ({n_list} listings"
                 f"{' + school' if n_school else ''})")

    return f"""
<!-- Inline map at bottom -->
<div id="map-container">
  <div class="map-header">{map_title}</div>
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
                *, price_data=None, map_markers=None, newest_html="",
                near_school_html="", auctions_html="", all_scored=None,
                all_listings=None, gone_html="", source_counts=None,
                market_pulse=None,
                health_pairs=None, n_auctions=None, auctions_failed=False,
                n_gone=None, today=None):
    """Build today's digest and write it to data/digests/. Returns (path, info)."""
    html = build_html(main_deals, still_active, comparison_html, status_note,
                      price_data=price_data, map_markers=map_markers,
                      newest_html=newest_html,
                      near_school_html=near_school_html,
                      auctions_html=auctions_html, all_scored=all_scored,
                      all_listings=all_listings, gone_html=gone_html,
                      source_counts=source_counts,
                      health_pairs=health_pairs, n_auctions=n_auctions,
                      auctions_failed=auctions_failed, n_gone=n_gone,
                      market_pulse=market_pulse, today=today)
    today = today or date.today().isoformat()
    digest_path = os.path.join(config.DIGEST_DIR, f"digest_{today}.html")
    utils.write_text(digest_path, html)
    info = f"digest saved to {digest_path}"
    print(f"[notifier] {info}")
    return digest_path, info
