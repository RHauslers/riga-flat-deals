# -*- coding: utf-8 -*-
"""Re-run the (fixed) geocoder over every cached MISS in data/geocode_cache.json.

One-off after the 2026-09-30 address-normalisation fix: the 35 entries with
lat=None were all SS.com transliteration/abbreviation casualties. Prints a
before/after table and rewrites the cache. ~1.1 s per Nominatim request.

Run:  python -X utf8 helper_scripts/regeocode_failed.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config   # noqa: E402
import geocode  # noqa: E402

DRY_RUN = False


def main():
    cache = geocode.load_cache()
    failed = {k: v for k, v in cache.items() if v.get("lat") is None}
    print(f"{len(cache)} cached addresses, {len(failed)} without coordinates")
    today = date.today().isoformat()
    fixed = still = 0
    for key, _old in sorted(failed.items()):
        street = key.split(":", 2)[2]
        lat, lon, precision, n = geocode._geocode_address(street)
        if lat is not None:
            fixed += 1
            km = geocode.haversine_km(lat, lon, geocode.config.SCHOOL_LAT,
                                      geocode.config.SCHOOL_LON)
            print(f"  OK   {key:55s} {precision:6s} {km:5.2f} km  ({n} req)")
        else:
            still += 1
            print(f"  MISS {key:55s} tried {n} candidate(s)")
        cache[key] = {"lat": lat, "lon": lon, "precision": precision,
                      "fetched_at": today}
    print(f"\nresolved {fixed}, still missing {still}")
    if not DRY_RUN:
        geocode.save_cache(cache)
        print(f"cache saved -> {config.GEOCODE_CACHE_JSON}")
    try:
        import pyperclip
        pyperclip.copy(f"regeocode_failed.py done: {fixed} resolved, {still} "
                       f"still missing. Feedback or next steps?")
    except Exception:
        pass


if __name__ == "__main__":
    main()
