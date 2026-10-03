# -*- coding: utf-8 -*-
"""Backfill flat_market_history.json from data/history.csv.

The per-district trend series ([date, median_ppu, median_price, ads] points)
started 2026-10-03, so the Market page's Δ 7d column and sparklines had
nothing to draw. history.csv already holds weeks of daily scrapes — this
script recomputes the same per-day, per-district medians from it (same
filters as the live path: sale only, in-budget, no new builds) and merges
them into flat_market_history.json.

Idempotent: existing dates are reconciled, so re-running is safe.

    python -X utf8 helper_scripts/backfill_flat_market.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config          # noqa: E402
import utils           # noqa: E402
import flat_market     # noqa: E402


def _rows_by_day(csv_path):
    """{scrape_date: {district: {source:id -> csv row}}} — sale rows only,
    deduped per source:id keeping the last row of the day (a same-day price
    change rewrites the listing, it doesn't double-count it)."""
    per_day = {}
    if not os.path.exists(csv_path):
        return per_day
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("deal_type") != "sale":
                continue
            district = (r.get("district") or "").strip()
            day = (r.get("scrape_date") or "").strip()
            if not district or not day:
                continue
            try:
                price = float(r.get("price_eur") or 0)
            except (ValueError, TypeError):
                continue
            if not price or price < config.MIN_SALE_PRICE_EUR:
                continue
            if utils.is_new_build(
                    {"series": r.get("series"), "title": r.get("title")}):
                continue
            key = f"{r.get('source')}:{r.get('id')}"
            per_day.setdefault(day, {}).setdefault(district, {})[key] = r
    return per_day


def main():
    per_day = _rows_by_day(config.HISTORY_CSV)
    hist = flat_market.load_history()
    added = updated = 0
    for day in sorted(per_day):
        for district, rows in per_day[day].items():
            ppu = utils.median([utils.to_float(r.get("price_per_m2"))
                                for r in rows.values()])
            price = utils.median([utils.to_float(r.get("price_eur"))
                                  for r in rows.values()])
            if ppu is None:
                continue
            pts = hist.setdefault(district, [])
            point = [day, ppu, price, len(rows)]
            for i, p in enumerate(pts):
                if p[0] == day:
                    if p != point:
                        pts[i] = point
                        updated += 1
                    break
            else:
                pts.append(point)
                added += 1
            pts.sort(key=lambda p: str(p[0]))
            del pts[:-config.FLAT_MARKET_HISTORY_MAX_POINTS]

    if added or updated:
        utils.write_json(config.FLAT_MARKET_HISTORY_JSON, hist, indent=None)
    print(f"[backfill] {added} point(s) added, {updated} reconciled, "
          f"{len(per_day)} day(s) scanned; districts: "
          f"{', '.join(sorted(hist)) or 'none'}")
    for d, pts in sorted(hist.items()):
        if pts:
            print(f"  {d}: {len(pts)} point(s), {pts[0][0]} .. {pts[-1][0]}")


if __name__ == "__main__":
    main()
