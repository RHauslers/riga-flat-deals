"""Per-district flat-market stats, rendered as a section on the Market
tab (docs/market.html) by website.build().

Symmetric to car_market.py: aggregates today's in-budget flat listings
per district — ad count, median EUR/m2, median ask, cheapest ad, and how
many were first seen today. main.run() writes the JSON; the page renders
from it, so no extra scraping is involved.
"""

import json
import os
import statistics
from html import escape as _e
from urllib.parse import quote as _q

import config


def _fmt_eur(v):
    if v is None:
        return "—"
    try:
        return "€{:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


def _median(values):
    vals = [float(v) for v in values if v is not None]
    try:
        return statistics.median(vals) if vals else None
    except (TypeError, ValueError):
        return None


def compute_district_stats(listings, price_data=None, today=None):
    """Group in-budget sale listings by district.

    Returns [{district, ads, median_ppu, median_price, min_price,
    min_url, new_today}], sorted by ads desc. ``price_data`` (the
    price_history map) supplies first_seen dates for the New count.
    """
    price_data = price_data or {}
    groups = {}
    for l in listings:
        if l.get("deal_type") != "sale":
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
        if today:
            for l in items:
                key = f"{l.get('source')}:{l.get('id')}"
                entry = price_data.get(key) or {}
                if entry.get("first_seen") == today:
                    new_today += 1
        stats.append({
            "district": district, "ads": len(items),
            "median_ppu": _median([l.get("price_per_m2") for l in items]),
            "median_price": _median(prices),
            "min_price": cheapest.get("price_eur") if cheapest else None,
            "min_url": (cheapest.get("url") or "") if cheapest else "",
            "new_today": new_today,
        })
    stats.sort(key=lambda s: (-s["ads"], s["median_ppu"] or 0))
    return stats


def flat_section_html(stats, run_date=None):
    """The flats block for the Market page — '' when no stats."""
    if not stats:
        return ""
    as_of = f" — as of {_e(str(run_date))}" if run_date else ""
    headers = ["District", "Ads", "New", "Median €/m²", "Median ask",
               "Cheapest"]
    head = "".join(
        "<th class='sort-th' style='padding:6px;{align}' "
        "onclick=\"sortTable('flat-market', {i})\">{name}</th>".format(
            i=i, name=_e(n),
            align="text-align:left" if i == 0 else "text-align:right")
        for i, n in enumerate(headers))
    rows = []
    for i, s in enumerate(stats):
        zebra = " style='background:#fafafa'" if i % 2 else ""
        cheap = (f"<a href='{_e(s['min_url'])}' target='_blank' "
                 f"rel='noopener noreferrer'>{_fmt_eur(s['min_price'])}"
                 f"</a>" if s.get("min_url") else _fmt_eur(s["min_price"]))
        d = _e(str(s["district"]))
        dist_l = (f"<a href='index.html?district={_q(str(s['district']))}'>"
                  f"{d}</a>")
        ppu = s["median_ppu"]
        rows.append(
            f"<tr{zebra}>"
            f"<td style='padding:6px' data-sort='{d}'>{dist_l}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['ads']}'>{s['ads']}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s.get('new_today') or 0}'>"
            f"{s.get('new_today') or 0}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{ppu or 0}'>"
            + (f"€{int(round(ppu)):,}" if ppu else "—") + "</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['median_price'] or 0}'>"
            f"{_fmt_eur(s['median_price'])}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['min_price'] or 0}'>{cheap}</td>"
            f"</tr>")
    return (
        f"<h2 id='flats'>Riga flat market — by district{as_of}</h2>"
        "<p class='note'>All in-budget sale listings from today's scan, "
        "grouped by district. Median €/m² is the asking-price reality "
        "check; <b>New</b> = ads first seen today. Clicking a district "
        "opens the Flats tab filtered to it.</p>"
        f"<table id='flat-market'><tr>{head}</tr>{''.join(rows)}</table>")


def save_stats(stats, run_date, total_ads, path=None):
    """Persist district stats for website.build()."""
    path = path or config.FLAT_MARKET_STATS_JSON
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": run_date, "total": total_ads,
                   "districts": stats}, f,
                  ensure_ascii=False, separators=(",", ":"))
    return path


def load_stats(path=None):
    path = path or config.FLAT_MARKET_STATS_JSON
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
