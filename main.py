# -*- coding: utf-8 -*-
"""
Flat_Searcher - daily orchestrator.

Pipeline (runs ONCE a day via .github/workflows/daily.yml; output is the
static website in docs/):
  1. Scrape ss.com + city24.lv for sales; filter implausible prices,
      exclude new builds, dedupe the same flat across portals.
  2. Update price history (CenuMednieks + our own tracking), geocode,
      compute school distance; scrape izsoles.ta.gov.lv auctions.
  3. Flat-source health check -> digest outage banner + loud CI log lines.
  4. Run the car digest (cars.run) — independent, never aborts flats.
  5. Training baseline = history BEFORE today (no leakage), then append
      today's rows; load seen_deals + last_digest state.
  6. Score ALL current listings -> today's true top N per deal type.
  7. Classify into main_deals (NEW/PRICE_DROP/REAPPEARED) + still_active;
      build comparison header and all HTML sections (map, newest,
      near-school, auctions, market stats, gone-tracking).
  8. Save the flat HTML digest; build the hosted site (docs/).
  9. Update state: seen_deals (today's shown prices/scores) + last_digest.
 10. Copy a status prompt to the clipboard (local runs only; no-op in CI).

Run locally:  python -X utf8 -m main
Run in CI:    python -X utf8 -m main
"""
import json
import os
import sys
import time
import traceback
from datetime import date

import config
import history
import scoring
import notifier
import classify
import website
import health
import utils
import price_history
import geocode
import cars
import flat_market
import gone
from scrapers import ss_com, city24, izsoles


def _acquire_run_lock():
    """data/.main.lock — stop two main.run() processes from interleaving
    writes to data/ and docs/ (observed to corrupt docs/cars.html).
    Returns True when this process owns the lock. A lock older than
    MAIN_LOCK_STALE_HOURS is treated as a crashed-run leftover and taken
    over; an unreadable lock never blocks the run."""
    try:
        if os.path.exists(config.MAIN_LOCK_FILE):
            with open(config.MAIN_LOCK_FILE, encoding="utf-8") as f:
                info = json.load(f)
            age_h = (time.time() - float(info.get("ts", 0))) / 3600
            if age_h < config.MAIN_LOCK_STALE_HOURS:
                print(f"[main] another run in progress "
                      f"(pid {info.get('pid')}, {info.get('date', '?')})"
                      " — aborting")
                return False
            print("[main] stale lock from a crashed run — taking over")
        with open(config.MAIN_LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "ts": time.time(),
                       "date": str(date.today())}, f)
        return True
    except (OSError, ValueError, TypeError):
        return True


def _release_run_lock():
    """Remove the lock only when we still own it."""
    try:
        with open(config.MAIN_LOCK_FILE, encoding="utf-8") as f:
            ours = json.load(f).get("pid") == os.getpid()
        if ours:
            os.remove(config.MAIN_LOCK_FILE)
    except (OSError, ValueError):
        pass


def _inject_chat(message):
    """Copy a status prompt to the clipboard, then paste it into the chat
    window when one is in the foreground (Windows only, best-effort;
    global rule 4 — never fatal)."""
    if not config.CHAT_INJECT_ENABLED:
        return
    try:
        import pyperclip
        pyperclip.copy(message)
    except Exception as e:
        print(f"[chat] clipboard inject skipped ({e})")
        return
    if os.environ.get("GITHUB_ACTIONS") or sys.platform != "win32":
        print("\n[chat] Status message copied to clipboard - paste into the chat:\n"
              f"    {message}\n")
        return
    try:
        import ctypes
        import re
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value or ""
        if re.search(config.CHAT_INJECT_WINDOW_RE, title, re.I):
            keyup = 0x0002  # KEYEVENTF_KEYUP
            vk_ctrl, vk_v, vk_ret = 0x11, 0x56, 0x0D
            user32.keybd_event(vk_ctrl, 0, 0, 0)
            user32.keybd_event(vk_v, 0, 0, 0)
            user32.keybd_event(vk_v, 0, keyup, 0)
            user32.keybd_event(vk_ctrl, 0, keyup, 0)
            if config.CHAT_INJECT_SUBMIT:
                user32.keybd_event(vk_ret, 0, 0, 0)
                user32.keybd_event(vk_ret, 0, keyup, 0)
            print(f"\n[chat] status pasted into '{title}' "
                  f"({'submitted' if config.CHAT_INJECT_SUBMIT else 'review + Enter to send'})\n")
        else:
            print("\n[chat] Status message copied to clipboard — no chat "
                  f"window in focus ('{title[:60]}'). Paste it yourself:\n"
                  f"    {message}\n")
    except Exception as e:
        print(f"[chat] paste skipped ({e}); message is on the clipboard:\n"
              f"    {message}\n")


def run():
    today = date.today().isoformat()
    _t0 = time.monotonic()
    if not _acquire_run_lock():
        return "Skipped — another run is in progress."
    try:
        return _run_body(today, _t0)
    finally:
        # Release unconditionally — an exception mid-run must not strand
        # the lock for MAIN_LOCK_STALE_HOURS and block the next run.
        _release_run_lock()


def _run_body(today, _t0):
    print(f"=== Flat_Searcher daily run {today} ===")

    # Step 1: scrape (tracking per-source counts for health checks; raw scraper
    #    exceptions are remembered for the digest outage banner)
    all_listings = []
    source_counts = {"ss.com": 0, "city24.lv": 0}
    source_errors = {}
    for dt in config.DEAL_TYPES:
        for src_name, scraper in (("ss.com", ss_com),
                                ("city24.lv", city24)):
            try:
                items = scraper.scrape(dt)
                all_listings.extend(items)
                for it in items:
                    src = it.get("source")
                    if src in source_counts:
                        source_counts[src] += 1
            except Exception as e:
                source_errors[src_name] = f"{type(e).__name__}: {e}"
                print(f"[main] {src_name} {dt} failed: {e}")
                traceback.print_exc()

    print(f"[main] total scraped (target districts): {len(all_listings)} "
          f"per source: {source_counts}")

    # Step 1 cont: sanity filter: drop listings with implausible prices only.
    #     No upper bound — the buyer's budget is applied by the browser-side
    #     budget tool, so every plausible listing is kept for scoring/embed.
    min_price = {"sale": config.MIN_SALE_PRICE_EUR, "rent": config.MIN_RENT_PRICE_EUR}
    before = len(all_listings)
    all_listings = [l for l in all_listings
                    if l.get("price_eur")
                    and l["price_eur"] >= min_price.get(l.get("deal_type"), 0)]
    dropped = before - len(all_listings)
    if dropped:
        print(f"[main] dropped {dropped} listing(s) with implausible/out-of-budget prices")

    # Step 1 cont: exclude newly built apartments (buyer's explicit requirement)
    all_listings, n_new_builds = utils.filter_new_builds(all_listings)
    if n_new_builds:
        print(f"[main] excluded {n_new_builds} new-build listing(s)")

    # Step 1 cont: merge the same flat listed on multiple portals (before history/scoring
    #     so it is not double-counted in the training baseline)
    all_listings, _n_merged = utils.dedupe_cross_source(all_listings)

    # Step 2: update price history (CenuMednieks backfill + our own daily tracking)
    price_data = price_history.update_price_history(all_listings)

    # Step 2 cont: geocode listings (city24 has coords from API, SS.com via Nominatim)
    if config.GEOCODE_ENABLED:
        all_listings = geocode.enrich_coordinates(all_listings)

    # Step 2 cont: compute distance to school for each listing (used in sale scoring
    #      and shown as a Distance column in the digest)
    geocode.annotate_school_km(all_listings)

    # Step 2 cont: state/bailiff auctions (izsoles.ta.gov.lv) — separate section,
    #      NOT part of the main ranking. Budget filter applies to the
    #      current bid (or start price when nobody has bid yet), then
    #      geocoded for distance to school + map markers.
    auctions = []
    auctions_failed = False
    if config.IZSOLES_ENABLED:
        try:
            auctions = izsoles.scrape()
        except Exception as e:
            auctions_failed = True
            print(f"[main] izsoles auction scrape failed: {e}")
            traceback.print_exc()
        before_a = len(auctions)
        auctions = [a for a in auctions
                    if a.get("price_eur")
                    and a["price_eur"] >= config.MIN_SALE_PRICE_EUR]
        if before_a and len(auctions) != before_a:
            print(f"[main] auctions: dropped {before_a - len(auctions)} "
                  f"out-of-budget auction(s)")
        if auctions and config.GEOCODE_ENABLED:
            auctions = geocode.enrich_coordinates(auctions)
        geocode.annotate_school_km(auctions)
        if auctions:
            print(f"[main] auctions: {len(auctions)} in-budget active "
                  f"auction(s) after filters")

    # Step 3: health check -> loud log line if a scraper (or the geocoder) looks
    #     broken. The same issue list feeds the digest's outage banner.
    geocoded = geocode.coverage(all_listings) if config.GEOCODE_ENABLED else None
    digest_issues = health.evaluate(source_counts, len(all_listings),
                                    geocoded=geocoded)
    for _src, _err in sorted(source_errors.items()):
        if not any(k == f"source_zero:{_src}" for k, _ in digest_issues):
            digest_issues.append((f"source_failed:{_src}", _err))
    # Consecutive-outage streaks: a source that produced nothing AND
    # raised nothing still counts as failed (a legit 0-day is rare two
    # days running). Streak >= 2 days escalates onto the banner — that
    # distinguishes a blip from a dead parser/block.
    _flat_ok = {f"flat:{s}": source_counts.get(s, 0) > 0
                and s not in source_errors
                for s in source_counts}
    if config.IZSOLES_ENABLED:
        _flat_ok["flat:izsoles"] = not auctions_failed
    for _key, _days in health.update_streaks(_flat_ok, today).items():
        _src = _key.split(":", 1)[1]
        digest_issues.append((
            f"outage_streak:{_key}",
            f"{_src} has failed {_days} days in a row — likely a dead "
            f"parser or a block, not a one-off blip."))
        print(f"[health] ISSUE outage_streak:{_key}: {_days} days")
    health.check(source_counts, len(all_listings), context="daily",
                 geocoded=geocoded)

    _t_scrape = time.monotonic()
    print(f"[main] flat scrape+enrich took {_t_scrape-_t0:.0f}s")

    # Step 4: the car digest is a self-contained sub-pipeline — a car
    # failure must never abort the flat digest, so it runs inside its own
    # try/except and only reports a status string back.
    car_status = ""
    try:
        car_status = cars.run()
    except Exception as e:
        print(f"[main] cars.run failed: {e}")
        traceback.print_exc()
    print(f"[main] cars.run took {time.monotonic()-_t_scrape:.0f}s")

    if not all_listings:
        try:
            website.build()
        except Exception as e:
            print(f"[main] website.build failed: {e}")
            traceback.print_exc()
        msg = ("Flat_Searcher finished with 0 listings today. "
               "Flat digest not updated. Check scrapers / site availability. Next steps?")
        _inject_chat(msg)
        return msg

    # Step 5: training baseline = everything scraped BEFORE today.
    #    Excluding today by DATE (not just "before this append") is essential:
    #    a manual rerun would otherwise have already inserted today's listings,
    #    and a listing would help define the average it is judged against,
    #    making genuine bargains look ordinary.
    hist_rows_all = history.load_history()          # one CSV read total
    hist_rows = [r for r in hist_rows_all if r.get('scrape_date') != today]
    history.append_history(all_listings,
                           latest_price=history.latest_prices(hist_rows_all))

    # Step 5 cont: load state
    seen_deals = history.load_seen_deals()
    last_digest = history.load_last_digest()
    print(f"[main] seen_deals: {len(seen_deals)} entries; "
          f"last_digest date: {(last_digest or {}).get('date', 'none')}")

    # Step 6: score ALL current listings -> today's true top N per deal type
    status_note = scoring.model_status(hist_rows)
    print(f"[main] {status_note}")
    all_scored = scoring.score_and_rank(all_listings, hist_rows)

    # District medians annotate each listing (_district_median_ppu) so
    # every later row builder can render a "vs district" chip; the stats
    # themselves are persisted further down for market.html. Computing
    # here (not at the save site) is required so Newest/near-school rows
    # — built before this section — see the annotation too.
    _flat_stats = flat_market.compute_district_stats(
        all_listings, price_data, today)
    _rent_stats = flat_market.compute_district_stats(
        all_listings, price_data, today, deal_type="rent")
    _district_ppu = {s["district"]: s.get("median_ppu")
                     for s in _flat_stats if s.get("median_ppu")}
    _district_rent = {s["district"]: s.get("median_price")
                      for s in _rent_stats if s.get("median_price")}
    for _l in all_listings:
        _l["_district_median_ppu"] = _district_ppu.get(_l.get("district"))
        _l["_district_rent_median"] = _district_rent.get(_l.get("district"))

    # Step 7: classify into main (badges) + still_active; build comparison header
    main_deals, still_active = classify.classify(all_scored, seen_deals, last_digest)
    comparison_html = classify.comparison_header(all_scored, last_digest)

    n_main = sum(len(v) for v in main_deals.values())
    n_still = sum(len(v) for v in still_active.values())
    print(f"[main] classified: {n_main} main (new/changed/reappeared), "
          f"{n_still} still active from yesterday")

    # Step 7 cont: build map markers from ALL listings with coordinates (not just
    #     the top N scored — the map should show everything we found, so
    #     the user can see all options at a glance).
    map_markers = []
    if config.MAP_ENABLED:
        # Attach scores to listings for map popups where available
        score_map = {}
        for dt, items in all_scored.items():
            for listing, score, method in items:
                score_map[utils.listing_key(listing)] = score
        for listing in all_listings:
            key = utils.listing_key(listing)
            listing["_score"] = score_map.get(key)
        map_markers = geocode.get_map_data(all_listings)
        # School marker (rendered distinctly on the map as the reference point)
        map_markers.insert(0, geocode.get_school_marker())
        # Auction markers (purple — state/bailiff auctions)
        if auctions:
            map_markers.extend(geocode.get_map_data(auctions))
        print(f"[main] map: {len(map_markers)} markers with coordinates "
              f"(of {len(all_listings)} total listings + 1 school "
              f"+ {len(auctions)} auctions)")

    # Step 7 cont: yesterday's live-ad snapshot supplies auction bid
    #     deltas AND the RELISTED match below (loaded once, before the
    #     flat_active.json overwrite further down).
    prev_active = utils.read_json(config.FLAT_ACTIVE_JSON, {})
    _prev_bids = {r["k"]: r["p"] for r in prev_active.get("rows", [])
                  if r.get("k", "").startswith("izsoles.")}
    # RELISTED: a flat live today under a NEW ad id that matches a
    # recently-gone ad (same district+street+rooms+~area) — the seller
    # withdrew and reposted. Annotated before every section builder so
    # each row can badge it.
    _prev_keys = {r.get("k") for r in prev_active.get("rows") or []}
    _relisted = gone.find_relisted(
        all_listings, _prev_keys, prev_active.get("recent_gone"))
    for _l in all_listings:
        _g = _relisted.get(utils.listing_key(_l))
        if _g:
            _l["_relisted"] = {"price": _g.get("p"), "gone": _g.get("gone")}
    if _relisted:
        print(f"[main] {len(_relisted)} relisted ad(s) detected "
              f"(withdrawn and reposted)")

    # Step 7 cont: build "Newest listings today" section (listings with NEW badge,
    #     ranked by deal score). This replaces the old "exceptional deals"
    #     composite score, which was misleading — it rewarded flats that
    #     were statistically cheap, but couldn't distinguish a genuine
    #     bargain from a trash flat that nobody wants.
    newest_html = notifier.build_newest_html(main_deals, price_data)
    print(f"[main] newest listings section built")

    # Step 7 cont: build "Walking distance to school" section: every in-budget
    #     listing within NEAR_SCHOOL_RADIUS_KM, sorted by distance. A
    #     fairly-priced flat scores ~0 on value and falls below the top-N
    #     cutoff even when it is exactly what the buyer needs (affordable,
    #     close to the school). This section guarantees visibility.
    #     Auctions within walking distance are included too — they are
    #     sales with coordinates like any other.
    near_school_html = notifier.build_near_school_html(all_listings + auctions)
    print(f"[main] near-school section built")

    # Step 7 cont: build "State & bailiff auctions" section (izsoles.ta.gov.lv):
    #     sorted by distance, budget-filtered, with start price / current
    #     bid / end date. Not part of the deal-score ranking.
    #     prev_active (loaded before the 6g overwrite below) supplies
    #     yesterday's auction bids -> NEW badges and bid-move deltas.
    # City-wide median EUR/m2 across today's plausible sale listings —
    # the market-relative signal for auction "vs market" chips and flat
    # "vs district" chips below (official appraisals are often stale).
    _city_median_ppu = utils.median(
        [utils.to_float(l.get("price_per_m2")) for l in all_listings
         if l.get("deal_type") == "sale" and l.get("price_per_m2")])
    auctions_html = notifier.build_auctions_html(
        auctions, failed=auctions_failed, prev_bids=_prev_bids,
        median_ppu=_city_median_ppu)
    print(f"[main] auctions section built")

    # Step 7 cont: district-level market stats for the Market tab
    #     (computed earlier so the rows could carry _district_median_ppu).
    #     City-wide medians feed the "Riga" history point — the digest's
    #     market-pulse KPI reads its Δ7d below.
    _city_median_ask = utils.median(
        [utils.to_float(l.get("price_eur")) for l in all_listings
         if l.get("deal_type") == "sale" and l.get("price_eur")])
    flat_market.save_stats(_flat_stats, today, len(all_listings),
                           city_ppu=_city_median_ppu,
                           city_price=_city_median_ask,
                           rent_stats=_rent_stats)
    _market_pulse = None
    if _city_median_ppu:
        _riga_hist = flat_market.load_history().get("Riga", [])
        # save_stats already appended today's point — [-2] is yesterday's.
        _ads_prev = (_riga_hist[-2][3]
                     if len(_riga_hist) > 1 and len(_riga_hist[-2]) > 3
                     else None)
        _market_pulse = {
            "ppu": _city_median_ppu,
            "delta": utils.delta_7d(_riga_hist),
            "ads": len(all_listings),
            "ads_delta": (len(all_listings) - _ads_prev
                          if _ads_prev is not None else None),
        }

    # Step 7 cont: "Disappeared — likely sold/removed": yesterday's live-ad ids
    #     minus today's, restricted to sources that produced data today.
    #     Auctions are tracked too — one that vanished usually ended or was
    #     settled, which is a real signal for the buyer. Today's rows are
    #     then written for tomorrow's comparison.
    live_now = all_listings + auctions
    ok_sources = {s for s, n in source_counts.items() if n > 0}
    if config.IZSOLES_ENABLED and not auctions_failed:
        ok_sources.add("izsoles.ta.gov.lv")
    gone_rows = gone.gone_rows(
        prev_active.get("rows"),
        {utils.listing_key(l) for l in live_now},
        ok_sources,
        config.GONE_MAX_ROWS)
    spike = health.gone_spike_issue(prev_active.get("rows"), gone_rows)
    if spike:
        digest_issues.append(spike)
        print(f"[health] ISSUE gone_spike: {spike[1]}")
    gone_html = notifier.build_gone_html(gone_rows, price_data, today)
    if gone_rows:
        print(f"[main] {len(gone_rows)} flat ad(s) disappeared since yesterday")
    live_rows = gone.flat_active_rows(live_now)
    utils.write_json(config.FLAT_ACTIVE_JSON,
                     {"date": today,
                      "rows": live_rows,
                      "recent_gone": gone.recent_gone_rows(
                          prev_active.get("recent_gone"),
                          gone_rows, today,
                          live_rows=live_rows)},
                     indent=None)

    # Step 8: save today's digest (pass price history + map markers + sections)
    _path, info = notifier.save_digest(main_deals, still_active, comparison_html,
                                       status_note, price_data=price_data,
                                       map_markers=map_markers,
                                       newest_html=newest_html,
                                       near_school_html=near_school_html,
                                       auctions_html=auctions_html,
                                       all_scored=all_scored,
                                       all_listings=all_listings,
                                       gone_html=gone_html,
                                       source_counts=source_counts,
                                       health_pairs=digest_issues,
                                       n_auctions=(len(auctions)
                                                   if config.IZSOLES_ENABLED
                                                   else None),
                                       auctions_failed=auctions_failed,
                                       n_gone=len(gone_rows),
                                       market_pulse=_market_pulse)

    # Step 8 cont: build hosted site (latest digest -> docs/index.html + archive)
    website.build()

    # Step 9: update state: record today's surfaced deals + save today's digest
    history.update_seen_deals(all_scored, seen_deals)
    history.save_last_digest(all_scored, today)

    _elapsed = time.monotonic() - _t0
    print(f"[main] total run time {_elapsed:.0f}s")
    msg = (f"Flat_Searcher run {today} complete ({_elapsed:.0f}s): "
           f"scraped {len(all_listings)}, "
           f"surfaced {n_main} new/changed + {n_still} still-active deals. "
           f"{info}. Scoring: {status_note}.{(' ' + car_status) if car_status else ''} "
           f"Feedback or next steps?")
    _inject_chat(msg)
    return msg


if __name__ == "__main__":
    out = run()
    sys.exit(0)
