# -*- coding: utf-8 -*-
"""
Car digest orchestration — produces the HTML page for docs/cars.html.

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
import os
import time
import traceback
from datetime import date, timedelta

import config
import car_value
import car_digest
import car_market
import gone
import health
import utils
from scrapers import car_ss, car_pp

SOURCES = (("ss.com", car_ss), ("pp.lv", car_pp))


def _read_json(path, default):
    return utils.read_json(path, default)


def _write_json(path, data):
    """Compact JSON (no indent): car_seen/snapshot are ~1 MB each when
    pretty-printed and are rewritten every day into git history."""
    utils.write_json(path, data, indent=None)


# ---------------------------------------------------------------------------
# car_seen.json — v2 compact layout (short keys, dates as day-offsets from
# _SEEN_EPOCH). Both files are rewritten daily into git history; the v1
# key/value format duplicated its six long key names on every one of ~7k
# entries. _read_seen() migrates v1 transparently on load.
# ---------------------------------------------------------------------------
_SEEN_EPOCH = date(2026, 1, 1)


def _seen_day(iso):
    """ISO date -> int days since _SEEN_EPOCH (None/unparseable -> None)."""
    try:
        return (date.fromisoformat(str(iso)) - _SEEN_EPOCH).days if iso else None
    except (ValueError, TypeError):
        return None


def _seen_iso(n):
    """Day offset -> ISO date (None -> None)."""
    try:
        return (_SEEN_EPOCH + timedelta(days=int(n))).isoformat() \
            if n is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _read_seen(path=None):
    """Load car_seen.json as the familiar {key: {first_seen, last_seen,
    last_price, last_shown, first_shown, prices}} dict. Reads the compact
    v2 layout and the old verbose one transparently."""
    data = _read_json(path or config.CAR_SEEN_JSON, {})
    if data.get("v") != 2:
        return data
    seen = {}
    for key, e in (data.get("entries") or {}).items():
        seen[key] = {
            "first_seen": _seen_iso(e.get("fs")),
            "last_seen": _seen_iso(e.get("ls")),
            "last_price": e.get("lp"),
            "last_shown": _seen_iso(e.get("lw")),
            "first_shown": _seen_iso(e.get("fw")),
            "prices": [[_seen_iso(p[0]), p[1]]
                       for p in (e.get("pr") or []) if len(p) == 2],
        }
    return seen


def _write_seen(seen, path=None):
    """Persist seen state in the v2 layout."""
    entries = {}
    for key, e in seen.items():
        entries[key] = {
            "fs": _seen_day(e.get("first_seen")),
            "ls": _seen_day(e.get("last_seen")),
            "lp": e.get("last_price"),
            "lw": _seen_day(e.get("last_shown")),
            "fw": _seen_day(e.get("first_shown")),
            "pr": [[_seen_day(p[0]), p[1]]
                    for p in (e.get("prices") or [])
                    if isinstance(p, (list, tuple)) and len(p) == 2],
        }
    _write_json(path or config.CAR_SEEN_JSON, {"v": 2, "entries": entries})


def load_snapshot(path=None):
    """Yesterday's market snapshot as {"date": ..., "listings": [...]}.
    Reads both the old {"listings": [dict, ...]} layout and the columnar
    v2 one ({v: 2, fields: [...], rows: [[...]]})."""
    data = _read_json(path or config.CAR_MARKET_SNAPSHOT_JSON, {})
    if data.get("v") == 2:
        fields = data.get("fields") or []
        return {"date": data.get("date"),
                "listings": [dict(zip(fields, row))
                             for row in data.get("rows") or []]}
    return data


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
    if (not isinstance(year, int)
            or not config.CAR_MIN_YEAR <= year <= date.today().year + 1):
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
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
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
    """Daily car scan. Returns a status string."""
    today = date.today().isoformat()
    print(f"[cars] car scan {today}")

    raw = {}
    source_errors = {}
    for name, scraper in SOURCES:
        # pp.lv is a Playwright browser session with no internal retry —
        # one second chance after a pause so a transient navigation
        # failure doesn't cost a whole day of that source's coverage.
        # SS.com retries internally (connection/timeout only).
        attempts = 2 if name == "pp.lv" else 1
        for attempt in range(attempts):
            try:
                raw[name] = scraper.scrape()
                break
            except Exception as e:
                if attempt + 1 < attempts:
                    print(f"[cars] {name} scrape failed ({e}); "
                          "retrying in 15 s")
                    time.sleep(15)
                    continue
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

    prev_snapshot = load_snapshot()  # before we overwrite: yesterday's ids
                                   # feed the "gone since yesterday" section

    deduped, n_merged = car_value.dedupe_cross_source(eligible_listings)
    if n_merged:
        print(f"[cars] merged {n_merged} cross-source duplicate(s)")

    # "Gone since yesterday": ids in yesterday's snapshot absent today —
    # only for sources that produced data (a silent source may have
    # failed, not sold out).
    ok_source_names = {n for n, _ in SOURCES if n not in source_errors}
    gone_car_rows = gone.gone_rows(
        gone.car_snapshot_rows(prev_snapshot.get("listings")),
        {gone.listing_key(l) for l in deduped},
        ok_source_names, config.CAR_GONE_MAX_ROWS)

    qualified, assessed = car_value.score_and_rank(deduped)

    _write_json(config.CAR_MARKET_SNAPSHOT_JSON,
                {"v": 2, "date": today,
                 "fields": list(config.CAR_SNAPSHOT_FIELDS),
                 "rows": [[l.get(f) for f in config.CAR_SNAPSHOT_FIELDS]
                          for l in deduped]})

    seen = _read_seen()
    badges = {}
    for l in qualified:
        key = f"{l.get('source')}:{l.get('id')}"
        badges[key] = _badge(seen.get(key), l.get("price_eur"), today)

    for l in deduped:
        key = f"{l.get('source')}:{l.get('id')}"
        entry = seen.setdefault(key, {"first_seen": today, "last_seen": today,
                                      "last_price": None, "last_shown": None,
                                      "first_shown": None, "prices": []})
        entry["last_seen"] = today
        entry["last_price"] = l.get("price_eur")
        # Price trail: one [date, price] point per sighting where the ask
        # actually changed (a same-day re-run only updates today's point).
        try:
            p = float(l.get("price_eur"))
        except (TypeError, ValueError):
            p = None
        hist = entry.setdefault("prices", [])
        if p is not None:
            if hist and hist[-1][0] == today:
                hist[-1][1] = p
            elif not hist or hist[-1][1] != p:
                hist.append([today, p])
        del hist[:-config.CAR_PRICE_HISTORY_MAX_POINTS]
    for l in qualified:
        entry = seen[f"{l.get('source')}:{l.get('id')}"]
        entry["first_shown"] = (entry.get("first_shown")
                                or entry.get("last_shown") or today)
        entry["last_shown"] = today
    # Expose the per-listing history to the digest (and through it to the
    # embedded market JSON the browser re-ranker uses). score_and_rank
    # returns COPIES, so annotate both the market rows and the scored ones.
    for l in deduped + assessed:
        entry = seen.get(f"{l.get('source')}:{l.get('id')}")
        if entry:
            l["_first_seen"] = entry.get("first_seen")
            l["_price_hist"] = entry.get("prices") or []

    # Per-model market stats for the Market tab (docs/market.html),
    # rendered by website.build() from this JSON.
    car_market.save_stats(
        car_market.compute_market_stats(deduped, qualified, today),
        today, len(deduped))

    health.check_cars(source_counts, source_errors)

    html_text = car_digest.build_html(qualified, assessed, source_counts,
                                      source_errors, badges, today,
                                      market=deduped, gone=gone_car_rows,
                                      seen=seen)
    path = _save_digest(html_text, today)
    cutoff = (date.today() - timedelta(days=config.CAR_SEEN_TTL_DAYS)).isoformat()
    stale = [k for k, v in seen.items()
             if (v.get("last_seen") or v.get("first_seen") or "") < cutoff]
    for k in stale:
        del seen[k]
    _write_seen(seen)

    counts = ", ".join(f"{n} {source_counts[n]['raw']}->{source_counts[n]['eligible']}"
                       for n, _ in SOURCES)
    msg = (f"cars: {counts} eligible; {len(qualified)} qualifying deals, "
           f"{len(assessed)} assessed; digest saved to {path}")
    if source_errors:
        msg += "; outage: " + "; ".join(f"{k}: {v}" for k, v in source_errors.items())
    print(f"[cars] {msg}")
    return msg
