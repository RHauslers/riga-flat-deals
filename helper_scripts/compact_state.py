# -*- coding: utf-8 -*-
"""One-off migration: rewrite the committed car state files to the v2
compact layouts (car_seen.json, car_market_snapshot.json).

    python -X utf8 helper_scripts/compact_state.py

Backs each file up to <name>.v1.bak first, then rewrites in place via the
same readers/writers cars.py uses, so the committed files shrink ~40-55%
and the next daily diff stays small. Safe to re-run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import cars
import utils

FILES = (
    (config.CAR_SEEN_JSON, "car_seen"),
    (config.CAR_MARKET_SNAPSHOT_JSON, "snapshot"),
)


def _backup(path):
    bak = path + ".v1.bak"
    if not os.path.exists(bak):
        with open(path, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
    return bak


def main():
    for path, label in FILES:
        if not os.path.exists(path):
            print(f"[compact] {label}: {path} missing — skipping")
            continue
        before = os.path.getsize(path)
        bak = _backup(path)
        if label == "car_seen":
            cars._write_seen(cars._read_seen(path), path)
        else:
            snap = cars.load_snapshot(path)
            utils.write_json(path, {
                "v": 2, "date": snap.get("date"),
                "fields": list(config.CAR_SNAPSHOT_FIELDS),
                "rows": [[l.get(f) for f in config.CAR_SNAPSHOT_FIELDS]
                         for l in snap.get("listings") or []]},
                indent=None)
        after = os.path.getsize(path)
        print(f"[compact] {label}: {before:,} -> {after:,} bytes "
              f"({100 * after / before:.0f}% of v1; backup {bak})")


if __name__ == "__main__":
    main()
