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


def enrich_inspection(listings, today):
    """Attach 'inspection' ('YYYY-MM', '' = none stated, None = unknown)
    to ss.com listings — the tehniskā apskate month lives on ad detail
    pages only, so results are cached per ad id and fetches are capped
    per run. pp.lv ads can't be read without a browser; they stay
    unknown."""
    cache = utils.read_json(config.CAR_TA_CACHE_JSON, {})
    fresh_cutoff = (date.fromisoformat(str(today)[:10])
                    - timedelta(days=config.CAR_TA_CACHE_DAYS)).isoformat()
    fetched = 0
    for l in listings:
        if l.get("inspection") is not None:
            continue
        key = utils.listing_key(l)
        entry = cache.get(key)
        if entry and (entry.get("f") or "") >= fresh_cutoff:
            l["inspection"] = entry.get("ta")
            continue
        if (l.get("source") != "ss.com" or not l.get("url")
                or fetched >= config.CAR_TA_MAX_FETCHES):
            continue
        try:
            ta = car_ss.fetch_inspection(l.get("url"))
        except car_ss.SourceBlocked as e:
            print(f"[cars] inspection fetch blocked: {e}")
            break
        except Exception as e:
            print(f"[cars] inspection fetch skipped for {key}: {e}")
            continue
        fetched += 1
        l["inspection"] = ta
        cache[key] = {"ta": ta, "f": str(today)[:10]}
    stale = [k for k, v in cache.items()
             if (v.get("f") or "") < fresh_cutoff]
    for k in stale:
        del cache[k]
    # Skip the write when nothing changed — otherwise every run (and every
    # cars.run() test that didn't fetch at all) rewrites the cache file.
    if fetched or stale:
        utils.write_json(config.CAR_TA_CACHE_JSON, cache, indent=None)
    if fetched:
        print(f"[cars] fetched tehniskā apskate for {fetched} ad(s)")


# ---------------------------------------------------------------------------
# car_seen.json — v2 compact layout (short keys, dates as day-offsets from
# _SEEN_EPOCH). Both files are rewritten daily into git history; the v1
# key/value format duplicated its six long key names on every one of ~7k
# entries. _read_seen() migrates v1 transparently on load.
# ---------------------------------------------------------------------------
_SEEN_EPOCH = date(2026, 1, 1)


def _new_seen_entry(today):
    """Fresh car_seen entry — the shape _read_seen() reconstructs for v2
    rows. One home so the write path can't drift from the read path."""
    return {"first_seen": today, "last_seen": today, "last_price": None,
            "last_shown": None, "first_shown": None, "prices": []}


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
    data = utils.read_json(path or config.CAR_SEEN_JSON, {})
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
    # Compact JSON (no indent): car_seen is ~1 MB pretty-printed and is
    # rewritten daily into git history.
    utils.write_json(path or config.CAR_SEEN_JSON,
                     {"v": 2, "entries": entries}, indent=None)


def load_snapshot(path=None):
    """Yesterday's market snapshot as {"date": ..., "listings": [...]}.
    Reads both the old {"listings": [dict, ...]} layout and the columnar
    v2 one ({v: 2, fields: [...], rows: [[...]]})."""
    data = utils.read_json(path or config.CAR_MARKET_SNAPSHOT_JSON, {})
    if data.get("v") == 2:
        fields = data.get("fields") or []
        return {"date": data.get("date"),
                "listings": [dict(zip(fields, row))
                             for row in data.get("rows") or []],
                # RELISTED detection needs this; dropping it silently
                # killed the feature every run after the first.
                "recent_gone": data.get("recent_gone")}
    return data


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
    path = os.path.join(config.DIGEST_DIR, f"cars_{today}.html")
    utils.write_text(path, html_text)  # makedirs inside write_text
    return path


def run(today=None):
    """Daily car scan. Returns a status string."""
    today = today or date.today().isoformat()
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
                try:
                    raw[name] = scraper.scrape(today=today)
                except TypeError:
                    raw[name] = scraper.scrape()
                break
            except Exception as e:
                if attempt + 1 < attempts:
                    print(f"[cars] {name} scrape failed ({e}); "
                          f"retrying in {config.CAR_PP_RETRY_DELAY_SECONDS} s")
                    time.sleep(config.CAR_PP_RETRY_DELAY_SECONDS)
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
            if car_value.eligible(l, today=today):
                eligible_listings.append(l)
                n_ok += 1
            else:
                reason = car_value.ineligible_reason(l, today=today)
                drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
        source_counts[name] = {"raw": len(items), "eligible": n_ok}
        if n_ok == 0 and name not in source_errors:
            source_errors[name] = \
                "no eligible car listings (source may be empty or parser changed)"
    source_counts["_drop_reasons"] = drop_reasons

    # Consecutive-outage streaks (same health_state.json as the flats —
    # "cars:" prefix keeps the two ss.com scrapes distinct). A streak
    # >= 2 days appends "· down Nd" to the source error so the outage
    # box says it's persistent, not a blip.
    _car_ok = {f"cars:{n}": n not in source_errors for n, _ in SOURCES}
    for _key, _days in health.update_streaks(_car_ok, today).items():
        _src = _key.split(":", 1)[1]
        if _src in source_errors:
            source_errors[_src] += f" · down {_days}d in a row"
        print(f"[health] ISSUE outage_streak:{_key}: {_days} days")

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
    today_keys = {utils.listing_key(l) for l in deduped}
    prev_car_rows = gone.car_snapshot_rows(prev_snapshot.get("listings"))
    gone_car_rows = gone.gone_rows(
        prev_car_rows, today_keys, ok_source_names, config.CAR_GONE_MAX_ROWS)
    # Partial scrape guardrail: an implausible overnight vanish share is
    # far more likely a clipped page set than a sales wave — warn rather
    # than trust the gone list (same check flats get in main.py).
    _spike = health.gone_spike_issue(prev_car_rows, gone_car_rows)
    if _spike:
        source_errors[_spike[0]] = _spike[1]
        print(f"[health] ISSUE gone_spike (cars): {_spike[1]}")

    # RELISTED — a car that vanished recently and is back under a new ad
    # id (same make/model/year/fuel + ~same odometer). Reposting is the
    # classic "needs to sell" move, and the fresh id wipes the price
    # trail, so the prior ask has to be reattached here for the drop
    # signal to see it. Annotate before scoring so the copies carry it.
    prev_snapshot_keys = {r.get("k") for r in prev_car_rows}
    relisted = gone.find_car_relisted(
        deduped, prev_snapshot_keys, prev_snapshot.get("recent_gone"))
    for l in deduped:
        r = relisted.get(utils.listing_key(l))
        if r:
            l["_relisted"] = {"p": r.get("p"), "gone": r.get("gone")}

    qualified, assessed = car_value.score_and_rank(deduped)

    # Tehniskā apskate for the digest rows — detail-page field, cached.
    # Fetching is capped, so only qualified rows are enriched; the values
    # are then copied onto the same-key deduped rows, which is what the
    # embedded market JSON (and therefore the browser custom view) reads.
    enrich_inspection(qualified, today)
    _insp = {utils.listing_key(l): l["inspection"] for l in qualified
             if l.get("inspection") is not None}
    for l in deduped:
        if l.get("inspection") is None:
            v = _insp.get(utils.listing_key(l))
            if v is not None:
                l["inspection"] = v

    utils.write_json(config.CAR_MARKET_SNAPSHOT_JSON,
                     {"v": 2, "date": today,
                      "fields": list(config.CAR_SNAPSHOT_FIELDS),
                      "rows": [[l.get(f) for f in config.CAR_SNAPSHOT_FIELDS]
                               for l in deduped],
                      "recent_gone": gone.recent_gone_rows(
                          prev_snapshot.get("recent_gone"), gone_car_rows,
                          today,
                          live_rows=gone.car_snapshot_rows(deduped))},
                     indent=None)

    seen = _read_seen()
    badges = {}
    for l in qualified:
        key = utils.listing_key(l)
        badges[key] = _badge(seen.get(key), l.get("price_eur"), today)

    for l in deduped:
        key = utils.listing_key(l)
        entry = seen.setdefault(key, _new_seen_entry(today))
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
        entry = seen[utils.listing_key(l)]
        entry["first_shown"] = (entry.get("first_shown")
                                or entry.get("last_shown") or today)
        entry["last_shown"] = today
    # Expose the per-listing history to the digest (and through it to the
    # embedded market JSON the browser re-ranker uses). score_and_rank
    # returns COPIES, so annotate both the market rows and the scored ones.
    for l in deduped + assessed:
        entry = seen.get(utils.listing_key(l))
        if entry:
            l["_first_seen"] = entry.get("first_seen")
            l["_price_hist"] = entry.get("prices") or []

    # Per-model market stats for the Market tab (docs/market.html),
    # rendered by website.build() from this JSON.
    _pool_median = utils.median(
        [utils.to_float(l.get("price_eur")) for l in deduped
         if l.get("price_eur")])
    car_market.save_stats(
        car_market.compute_market_stats(deduped, qualified, today),
        today, len(deduped), pool_median=_pool_median)
    _car_pulse = None
    if _pool_median:
        _all_hist = car_market.load_history().get("_all", [])
        _ADS_IDX = 2
        _ads_prev = (_all_hist[-2][_ADS_IDX]
                     if len(_all_hist) > 1 and len(_all_hist[-2]) > _ADS_IDX
                     else None)
        _car_pulse = {
            "median": _pool_median,
            "delta": utils.delta_7d(_all_hist),
            "ads": len(deduped),
            "ads_delta": (len(deduped) - _ads_prev
                          if _ads_prev is not None else None),
        }

    health.check_cars(source_counts, source_errors)

    html_text = car_digest.build_html(qualified, assessed, source_counts,
                                      source_errors, badges, today,
                                      market=deduped, gone=gone_car_rows,
                                      seen=seen, market_pulse=_car_pulse)
    path = _save_digest(html_text, today)
    cutoff = (date.fromisoformat(str(today)[:10])
              - timedelta(days=config.CAR_SEEN_TTL_DAYS)).isoformat()
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
