# -*- coding: utf-8 -*-
"""Seed data/car_market_history.json from archived car digests.

Every data/digests/cars_YYYY-MM-DD.html embeds that day's deduplicated
eligible market as <script id="car-market-data"> JSON (dict-encoded
rows). Replaying those embeds through the same (make|model -> median
ask) aggregation car_market.compute_market_stats uses backfills days of
per-model history — the Δ 7d column and trend sparklines on
docs/market.html light up immediately instead of after a week of runs.

Idempotent: utils.upsert_history_point replaces a same-date point, so
re-running after a new digest lands only adds/freshens points. The
script never touches data/digests/ — it only reads them.

Run:  python -X utf8 helper_scripts/backfill_car_market.py
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import car_market
import utils

# ---------------------------------------------------------------------------
# Settings (hard-coded — adjust here, not via CLI args)
# ---------------------------------------------------------------------------
DIGEST_GLOB = os.path.join(config.DIGEST_DIR, "cars_*.html")
HISTORY_JSON = config.CAR_MARKET_HISTORY_JSON
MIN_LISTINGS = config.CAR_MARKET_MIN_LISTINGS
MAX_POINTS = config.CAR_MARKET_HISTORY_MAX_POINTS
EMBED_RE = re.compile(
    r'<script type="application/json" id="car-market-data">(.*?)</script>',
    re.S)
FILE_DATE_RE = re.compile(r"cars_(\d{4}-\d{2}-\d{2})\.html$")


def _decode_listings(payload):
    """car-market-data payload -> [{make, model, price_eur, ...}].

    Rows are positional against payload['fields']; string columns named
    in payload['dict'] carry integer dictionary indices.
    """
    fields = payload.get("fields") or []
    dicts = payload.get("dict") or {}
    out = []
    for row in payload.get("rows") or []:
        l = dict(zip(fields, row))
        for f, table in dicts.items():
            v = l.get(f)
            if isinstance(v, int) and 0 <= v < len(table):
                l[f] = table[v]
        out.append(l)
    return out


def _model_medians(listings):
    """{make|model: median_ask} over the eligible pool — the same
    grouping + noise floor car_market.compute_market_stats applies."""
    groups = {}
    for l in listings:
        key = car_market._group_key(l)
        if not all(key):
            continue
        groups.setdefault(key, []).append(l)
    medians = {}
    for key, items in groups.items():
        if len(items) < MIN_LISTINGS:
            continue
        med = utils.median(
            [l.get("price_eur") for l in items if l.get("price_eur")])
        if med:
            medians[f"{key[0]}|{key[1]}"] = med
    return medians


def main():
    files = sorted(glob.glob(DIGEST_GLOB))
    if not files:
        print(f"[backfill] no car digests under {DIGEST_GLOB}")
        return
    hist = utils.read_json(HISTORY_JSON, {})
    n_days = n_points = 0
    for path in files:
        m = FILE_DATE_RE.search(path)
        if not m:
            continue
        run_date = m.group(1)
        try:
            with open(path, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            continue
        em = EMBED_RE.search(html)
        if not em:
            print(f"[backfill] {run_date}: no embed (old digest?) — skip")
            continue
        try:
            payload = json.loads(em.group(1))
        except ValueError:
            print(f"[backfill] {run_date}: unparseable embed — skip")
            continue
        medians = _model_medians(_decode_listings(payload))
        for key, med in medians.items():
            pts = hist.setdefault(key, [])
            utils.upsert_history_point(
                pts, run_date, [run_date, med], MAX_POINTS)
            n_points += 1
        n_days += 1
        print(f"[backfill] {run_date}: {len(medians)} model medians")
    utils.write_json(HISTORY_JSON, hist, indent=None)
    models = len(hist)
    print(f"[backfill] done: {n_days} day(s), {n_points} point(s) "
          f"merged into {models} model series -> {HISTORY_JSON}")


if __name__ == "__main__":
    main()
