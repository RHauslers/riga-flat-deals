"""Per-district flat-market stats, rendered as a section on the Market
tab (docs/market.html) by website.build().

Symmetric to car_market.py: aggregates today's in-budget flat listings
per district — ad count, median EUR/m2, median ask, cheapest ad, and how
many were first seen today. main.run() writes the JSON; the page renders
from it, so no extra scraping is involved.
"""

from datetime import date
from html import escape as _e
from urllib.parse import quote as _q

import config
import utils


def compute_district_stats(listings, price_data=None, today=None,
                           deal_type="sale"):
    """Group in-budget listings of ``deal_type`` by district.

    Returns [{district, ads, median_ppu, median_price, min_price,
    min_url, new_today}], sorted by ads desc. ``price_data`` (the
    price_history map) supplies first_seen dates for the New count.
    """
    price_data = price_data or {}
    groups = {}
    for l in listings:
        if l.get("deal_type") != deal_type:
            continue
        d = str(l.get("district") or "").strip()
        if not d:
            continue
        groups.setdefault(d, []).append(l)

    stats = []
    for district, items in groups.items():
        prices = [l.get("price_eur") for l in items if l.get("price_eur")]
        cheapest = min((l for l in items if l.get("price_eur")),
                       key=lambda l: l["price_eur"], default=None)
        new_today = 0
        ages = []
        n_cuts = 0
        for l in items:
            key = utils.listing_key(l)
            entry = price_data.get(key) or {}
            if today and entry.get("first_seen") == today:
                new_today += 1
            # Market temperature: how old the district's ads are on
            # average (cenu days_on_market, else our own first_seen) and
            # how many have already cut their ask. A district full of
            # stale + cutting ads is where negotiation room lives.
            cenu = entry.get("cenumednieks") or {}
            age = cenu.get("days_on_market")
            if age is None and entry.get("first_seen"):
                _t = (date.fromisoformat(today)
                      if isinstance(today, str) else today)
                age = utils.days_since(entry["first_seen"], _t)
            if age is not None:
                ages.append(age)
            info = utils.flat_motivated(entry)
            if info and info.get("drop_eur"):
                n_cuts += 1
        stats.append({
            "district": district, "ads": len(items),
            "median_ppu": utils.median([l.get("price_per_m2") for l in items]),
            "median_price": utils.median(prices),
            "min_price": cheapest.get("price_eur") if cheapest else None,
            "min_url": (cheapest.get("url") or "") if cheapest else "",
            "new_today": new_today,
            "median_age": utils.median(ages),
            "cut_pct": round(100.0 * n_cuts / len(items)) if items else 0,
        })
    stats.sort(key=lambda s: (-s["ads"], s["median_ppu"] or 0))
    return stats


def _stats_table_html(stats, history, table_id, hist_prefix="",
                      rent_median_by_district=None):
    """Shared per-district stats table (sale and rent differ only in
    which series prefix their history uses). ``rent_median_by_district``
    ({district: EUR/mo median}) adds a gross-yield column on the sale
    table — rent median ×12 ÷ sale median ask."""
    headers = ["District", "Ads", "New", "Median €/m²", "Δ 7d", "Trend",
               "Med. days", "Cuts", "Median ask", "Cheapest"]
    if rent_median_by_district:
        headers.append("Yield")
    head = "".join(
        "<th class='sort-th' style='padding:6px;{align}' "
        "onclick=\"sortTable('{tid}', {i})\">{name}</th>".format(
            tid=table_id, i=i, name=_e(n),
            align="text-align:left" if i == 0 else "text-align:right")
        for i, n in enumerate(headers))
    rows = []
    for i, s in enumerate(stats):
        zebra = " class='z'" if i % 2 else ""
        _min_url = utils.safe_url(s.get("min_url"))
        cheap = (f"<a href='{_e(_min_url)}' target='_blank' "
                 f"rel='noopener noreferrer'>{utils.fmt_eur(s['min_price'])}"
                 f"</a>" if _min_url else utils.fmt_eur(s["min_price"]))
        d = _e(str(s["district"]))
        # Sort key: raw district name — sorting on the escaped form
        # orders entities before letters (Ā/ģ names landed wrong).
        d_raw = str(s["district"]).lower()
        dist_l = (f"<a href='index.html?district={_q(str(s['district']))}'>"
                  f"{d}</a>")
        ppu = s["median_ppu"]
        pts = history.get(hist_prefix + str(s["district"]), [])
        delta = utils.delta_7d(pts)
        delta_html = "—"
        delta_sort = 0.0
        if delta is not None:
            delta_sort = delta
            # Buyer's POV, same as the car table: a falling district
            # median means prices are coming to the buyer = green.
            color = "var(--good)" if delta < 0 else \
                "var(--bad)" if delta > 0 else "var(--muted)"
            delta_html = f"<span style='color:{color}'>{delta:+.1f}%</span>"
        spark = utils.sparkline_svg(
            [(p[0], p[1]) for p in pts], title="median €/m²")
        yield_html, yield_sort = "—", -1.0
        if rent_median_by_district is not None:
            rm = rent_median_by_district.get(s["district"])
            if rm and s.get("median_price"):
                y = rm * 12 / s["median_price"] * 100
                yield_sort, yield_html = round(y, 1), f"~{y:.1f}%"
        rows.append(
            f"<tr{zebra}>"
            f"<td style='padding:6px' data-sort='{_e(d_raw)}'>{dist_l}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['ads']}'>{s['ads']}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s.get('new_today') or 0}'>"
            f"{s.get('new_today') or 0}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{ppu or 0}'>"
            + (f"€{int(round(ppu)):,}" if ppu else "—") + "</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{delta_sort:.2f}'>{delta_html}</td>"
            f"<td style='padding:6px' data-sort='{delta_sort:.2f}'>"
            f"{spark}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s.get('median_age') or 0}'>"
            + (f"{int(round(s['median_age']))} d"
               if s.get("median_age") is not None else "—") + "</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s.get('cut_pct') or 0}'>"
            f"{s.get('cut_pct') or 0}%</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['median_price'] or 0}'>"
            f"{utils.fmt_eur(s['median_price'])}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['min_price'] or 0}'>{cheap}</td>"
            + (f"<td style='padding:6px;text-align:right' "
               f"data-sort='{yield_sort:.1f}'>{yield_html}</td>"
               if rent_median_by_district else "")
            + f"</tr>")
    return f"<table id='{table_id}'><tr>{head}</tr>{''.join(rows)}</table>"


def flat_section_html(stats, run_date=None, history=None, rent_stats=None):
    """The flats block for the Market page — '' when no stats. Sale and
    rent districts get one table each (rent trends live under rent:*)."""
    if not stats and not rent_stats:
        return ""
    history = history or {}
    as_of = f" — as of {_e(str(run_date))}" if run_date else ""
    sale_html = ""
    if stats:
        sale_html = (
            f"<h2 id='flats'>Riga flat market — by district{as_of}</h2>"
            "<p class='note'>All in-budget sale listings from today's scan, "
            "grouped by district. Median €/m² is the asking-price reality "
            "check; <b>New</b> = ads first seen today; <b>Δ 7d</b> = median "
            "€/m² change over the last week; <b>Trend</b> = the same as a "
            "sparkline. <b>Med. days</b> = median ad age (market "
            "temperature); <b>Cuts</b> = share of ads that already cut their "
            "ask — high on both means sellers are waiting and negotiating. "
            "<b>Yield</b> = rough gross rental yield (district median rent "
            "×12 ÷ median sale ask — before costs/vacancy). "
            "Clicking a district opens the Flats tab filtered to it.</p>"
            + _stats_table_html(
                stats, history, "flat-market",
                rent_median_by_district={
                    str(s["district"]): s["median_price"]
                    for s in (rent_stats or []) if s.get("median_price")}))
    rent_html = ""
    if rent_stats:
        rent_html = (
            "<h2 id='flats-rent' style='margin-top:24px'>Riga rent market"
            " — by district</h2>"
            "<p class='note'>The same view over rent listings (monthly "
            "asking prices; €/m² is per month too). Districts link opens "
            "the Flats tab filtered to that district — pick “For rent” "
            "there.</p>"
            + _stats_table_html(rent_stats, history, "flat-market-rent",
                                hist_prefix="rent:"))
    return sale_html + rent_html


def save_stats(stats, run_date, total_ads, path=None,
               city_ppu=None, city_price=None, rent_stats=None):
    """Persist district stats for website.build(). Also appends today's
    per-district medians to flat_market_history.json so the page can draw
    trend sparklines once a district has been tracked on multiple days.
    ``city_ppu``/``city_price`` also record a whole-city "Riga" point so
    the digest can quote a market-wide median + Δ7d. ``rent_stats`` (the
    same shape, computed over rent listings) is persisted alongside under
    "rent_districts" and tracked under "rent:<district>" history keys."""
    path = path or config.FLAT_MARKET_STATS_JSON
    utils.write_json(path, {"date": run_date, "total": total_ads,
                            "districts": stats,
                            "rent_districts": rent_stats or []},
                     indent=None)
    _append_history(stats, run_date, city_ppu=city_ppu,
                    city_price=city_price, total_ads=total_ads,
                    rent_stats=rent_stats)
    return path


def _append_history(stats, run_date, path=None, city_ppu=None,
                    city_price=None, total_ads=None, rent_stats=None):
    """flat_market_history.json: {district: [[date, median_ppu,
    median_price, ads], ...]}, capped at FLAT_MARKET_HISTORY_MAX_POINTS.
    "Riga" is a reserved pseudo-district holding the city-wide series;
    rent districts are tracked separately under "rent:<district>"."""
    path = path or config.FLAT_MARKET_HISTORY_JSON
    hist = utils.read_json(path, {})
    for s in stats:
        if s.get("median_ppu") is None:
            continue
        pts = hist.setdefault(str(s["district"]), [])
        point = [run_date, s["median_ppu"], s.get("median_price"),
                 s["ads"]]
        utils.upsert_history_point(
            pts, run_date, point, config.FLAT_MARKET_HISTORY_MAX_POINTS)
    for s in rent_stats or []:
        if s.get("median_ppu") is None:
            continue
        pts = hist.setdefault("rent:" + str(s["district"]), [])
        utils.upsert_history_point(
            pts, run_date,
            [run_date, s["median_ppu"], s.get("median_price"), s["ads"]],
            config.FLAT_MARKET_HISTORY_MAX_POINTS)
    if city_ppu is not None:
        utils.upsert_history_point(
            hist.setdefault("Riga", []), run_date,
            [run_date, city_ppu, city_price, total_ads],
            config.FLAT_MARKET_HISTORY_MAX_POINTS)
    try:
        utils.write_json(path, hist, indent=None)
    except OSError:
        pass


def load_history(path=None):
    """{district: [[date, median_ppu, median_price, ads], ...]}."""
    return utils.read_json(path or config.FLAT_MARKET_HISTORY_JSON, {})


def load_stats(path=None):
    path = path or config.FLAT_MARKET_STATS_JSON
    return utils.read_json(path, None)
