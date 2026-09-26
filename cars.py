# -*- coding: utf-8 -*-
"""
Car digest orchestration — website only (no car email is ever sent).

run() -> status string:
  1. Scrape ss.com (/lv/ only) and pp.lv, catching each source separately so
     one outage does not kill the other.
  2. Keep only listings passing car_value.eligible (per-source raw/eligible
     counts and drop reasons are recorded for the digest). A source that
     raises OR yields zero eligible listings is marked failed for this run.
  3. Cross-source dedupe BEFORE scoring (car_value.dedupe_cross_source).
  4. Score all affordable candidates against comparable current asking
     prices (car_value.score_and_rank).
  5. Annotate NEW / PRICE DROP / STILL ACTIVE / REAPPEARED badges from
     data/car_seen.json, save data/digests/cars_YYYY-MM-DD.html, then update
     the seen state (only when at least one source produced eligible data).
"""
import json
import os
import traceback
from datetime import date, timedelta

import config
import car_value
import car_digest
from scrapers import car_ss, car_pp

SOURCES = (("ss.com", car_ss), ("pp.lv", car_pp))


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ineligible_reason(l):
    """Coarse single reason a listing failed car_value.eligible()."""
    price, year = l.get("price_eur"), l.get("year")
    mileage = l.get("mileage_km")
    if price is None:
        return "missing price"
    try:
        if not (config.CAR_MIN_PRICE_EUR <= float(price) <= config.CAR_COMPARABLE_MAX_PRICE_EUR):
            return "price out of range"
    except (TypeError, ValueError):
        return "missing price"
    if not isinstance(year, int) or year < config.CAR_MIN_YEAR:
        return "year out of range"
    if mileage is None:
        return "missing mileage"
    try:
        if float(mileage) > config.CAR_MAX_MILEAGE_KM:
            return "mileage too high"
    except (TypeError, ValueError):
        return "missing mileage"
    if not l.get("make") or not l.get("model"):
        return "missing make/model"
    if l.get("fuel") not in ("petrol", "diesel", "hybrid", "electric", "lpg"):
        return "unknown fuel"
    if l.get("fuel") != "electric" and not l.get("engine_l"):
        return "missing engine"
    return "ineligible"


def _badge(entry, price, today=None):
    """Badge for a qualified listing based on its previous shown state."""
    today = today or date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if not entry or not entry.get("last_shown"):
        return "NEW"
    prev = entry.get("last_price")
    try:
        prev_f = float(prev) if prev is not None else None
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        prev_f = price_f = None
    if prev_f and price_f is not None \
            and price_f <= prev_f * (1 - config.PRICE_DROP_MIN_PCT / 100.0):
        return "PRICE DROP"
    if entry["last_shown"] == today and (
            entry.get("first_shown") == today
            or ("first_shown" not in entry
                and entry.get("first_seen") == today)):
        return "NEW"
    if prev_f is not None and price_f == prev_f \
            and entry["last_shown"] in (today, yesterday):
        return "STILL ACTIVE"
    return "REAPPEARED"


def _save_digest(html_text, today):
    os.makedirs(config.DIGEST_DIR, exist_ok=True)
    path = os.path.join(config.DIGEST_DIR, f"cars_{today}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path


def run():
    """Daily car scan. Returns a status string; never sends email."""
    today = date.today().isoformat()
    print(f"[cars] car scan {today}")

    raw = {}
    source_errors = {}
    for name, scraper in SOURCES:
        try:
            raw[name] = scraper.scrape()
        except Exception as e:
            raw[name] = []
            source_errors[name] = f"{type(e).__name__}: {e}"
            print(f"[cars] {name} scrape failed: {e}")
            traceback.print_exc()

    source_counts = {}
    drop_reasons = {}
    eligible_listings = []
    for name, items in raw.items():
        n_ok = 0
        for l in items:
            if car_value.eligible(l):
                eligible_listings.append(l)
                n_ok += 1
            else:
                reason = _ineligible_reason(l)
                drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        source_counts[name] = {"raw": len(items), "eligible": n_ok}
        if n_ok == 0 and name not in source_errors:
            source_errors[name] = \
                "no eligible car listings (source may be empty or parser changed)"
    source_counts["_drop_reasons"] = drop_reasons

    ok_sources = [n for n, _ in SOURCES if n not in source_errors]
    if not ok_sources:
        html_text = car_digest.build_html([], [], source_counts, source_errors,
                                          {}, today)
        path = _save_digest(html_text, today)
        msg = (f"cars: both sources failed "
               f"({'; '.join(f'{k}: {v}' for k, v in source_errors.items())}); "
               f"error page saved to {path}")
        print(f"[cars] {msg}")
        return msg

    deduped, n_merged = car_value.dedupe_cross_source(eligible_listings)
    if n_merged:
        print(f"[cars] merged {n_merged} cross-source duplicate(s)")

    qualified, assessed = car_value.score_and_rank(deduped)

    snapshot = {"date": today, "listings": [
        {field: l.get(field) for field in config.CAR_SNAPSHOT_FIELDS}
        for l in deduped]}
    _write_json(config.CAR_MARKET_SNAPSHOT_JSON, snapshot)

    seen = _read_json(config.CAR_SEEN_JSON, {})
    badges = {}
    for l in qualified:
        key = f"{l.get('source')}:{l.get('id')}"
        badges[key] = _badge(seen.get(key), l.get("price_eur"), today)

    html_text = car_digest.build_html(qualified, assessed, source_counts,
                                      source_errors, badges, today)
    path = _save_digest(html_text, today)

    for l in deduped:
        key = f"{l.get('source')}:{l.get('id')}"
        entry = seen.setdefault(key, {"first_seen": today, "last_seen": today,
                                      "last_price": None, "last_shown": None,
                                      "first_shown": None})
        entry["last_seen"] = today
        entry["last_price"] = l.get("price_eur")
    for l in qualified:
        entry = seen[f"{l.get('source')}:{l.get('id')}"]
        entry["first_shown"] = (entry.get("first_shown")
                                or entry.get("last_shown") or today)
        entry["last_shown"] = today
    cutoff = (date.today() - timedelta(days=config.CAR_SEEN_TTL_DAYS)).isoformat()
    stale = [k for k, v in seen.items()
             if (v.get("last_seen") or v.get("first_seen") or "") < cutoff]
    for k in stale:
        del seen[k]
    _write_json(config.CAR_SEEN_JSON, seen)

    counts = ", ".join(f"{n} {source_counts[n]['raw']}->{source_counts[n]['eligible']}"
                       for n, _ in SOURCES)
    msg = (f"cars: {counts} eligible; {len(qualified)} qualifying deals, "
           f"{len(assessed)} assessed; digest saved to {path}")
    if source_errors:
        msg += "; outage: " + "; ".join(f"{k}: {v}" for k, v in source_errors.items())
    print(f"[cars] {msg}")
    return msg
