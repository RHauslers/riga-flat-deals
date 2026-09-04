# -*- coding: utf-8 -*-
"""
Flat_Searcher - daily orchestrator.

Pipeline:
  1. Scrape ss.com + city24.lv for each deal type (rent, sale).
  2. Load history BEFORE appending (no leakage), then append today's rows.
  3. Migrate legacy seen_ids.json -> seen_deals.json (one-time, no-op after).
  4. Load state: seen_deals, last_digest (yesterday's top deals).
  5. Score ALL current listings (not just new) -> today's true top N.
  6. Classify into main_deals (NEW/PRICE_DROP/REAPPEARED) + still_active.
     Build "vs yesterday" comparison header.
  7. Notify: send email (skips if recipient unsubscribed), save HTML digest.
  8. Update state: seen_deals (today's shown prices/scores) + last_digest.
  9. Inject a status prompt into the Cascade/Devin chat (clipboard).

Run locally:  python -m main
Run in CI:    python -m main
"""
import sys
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
from scrapers import ss_com, city24


def _inject_chat(message):
    """Copy a status prompt to the clipboard so it can be pasted into the chat.
    (Per global rule 4 - best-effort, never fatal.)"""
    if not config.CHAT_INJECT_ENABLED:
        return
    try:
        import pyperclip
        pyperclip.copy(message)
        print("\n[chat] Status message copied to clipboard - paste into the chat:\n"
              f"    {message}\n")
    except Exception as e:
        print(f"[chat] clipboard inject skipped ({e})")


def run():
    today = date.today().isoformat()
    print(f"=== Flat_Searcher daily run {today} ===")

    # 1. Scrape (tracking per-source counts for health checks)
    all_listings = []
    source_counts = {"ss.com": 0, "city24.lv": 0}
    for dt in config.DEAL_TYPES:
        for scraper in (ss_com, city24):
            try:
                items = scraper.scrape(dt)
                all_listings.extend(items)
                for it in items:
                    src = it.get("source")
                    if src in source_counts:
                        source_counts[src] += 1
            except Exception as e:
                print(f"[main] {scraper.__name__} {dt} failed: {e}")
                traceback.print_exc()

    print(f"[main] total scraped (target districts): {len(all_listings)} "
          f"per source: {source_counts}")

    # 1b. Sanity filter: drop listings with implausible prices
    min_price = {"sale": config.MIN_SALE_PRICE_EUR, "rent": config.MIN_RENT_PRICE_EUR}
    before = len(all_listings)
    all_listings = [l for l in all_listings
                    if l.get("price_eur") and l["price_eur"] >= min_price.get(l.get("deal_type"), 0)]
    dropped = before - len(all_listings)
    if dropped:
        print(f"[main] dropped {dropped} listing(s) with implausible prices")

    # 1c. Merge the same flat listed on multiple portals (before history/scoring
    #     so it is not double-counted in the training baseline)
    all_listings, _n_merged = utils.dedupe_cross_source(all_listings)

    # 1d. Update price history (CenuMednieks backfill + our own daily tracking)
    price_data = price_history.update_price_history(all_listings)

    # 1e. Geocode listings (city24 has coords from API, SS.com via Nominatim)
    if config.GEOCODE_ENABLED:
        all_listings = geocode.enrich_coordinates(all_listings)

    # 1f. Health check -> alerts the OPERATOR if a scraper looks broken
    health.check_and_alert(source_counts, len(all_listings), context="daily")

    if not all_listings:
        msg = ("Flat_Searcher finished with 0 listings today. "
               "No email sent. Check scrapers / site availability. Next steps?")
        _inject_chat(msg)
        return msg

    # 2. Training baseline = everything scraped BEFORE today.
    #    Excluding today by DATE (not just "before this append") is essential:
    #    the hourly escalation scan also appends to history.csv, so by 10:00 it
    #    has already inserted today's listings. Without exclude_today a listing
    #    would help define the average it is judged against, making genuine
    #    bargains look ordinary.
    hist_rows = history.load_history(exclude_today=True)
    history.append_history(all_listings)

    # 3. One-time migration of legacy seen_ids.json -> seen_deals.json
    history.migrate_seen_ids()

    # 4. Load state
    seen_deals = history.load_seen_deals()
    last_digest = history.load_last_digest()
    print(f"[main] seen_deals: {len(seen_deals)} entries; "
          f"last_digest date: {(last_digest or {}).get('date', 'none')}")

    # 5. Score ALL current listings -> today's true top N per deal type
    status_note = scoring.model_status(hist_rows)
    print(f"[main] {status_note}")
    all_scored = scoring.score_and_rank(all_listings, hist_rows)

    # 6. Classify into main (badges) + still_active; build comparison header
    main_deals, still_active = classify.classify(all_scored, seen_deals, last_digest)
    comparison_html = classify.comparison_header(all_scored, last_digest)

    n_main = sum(len(v) for v in main_deals.values())
    n_still = sum(len(v) for v in still_active.values())
    print(f"[main] classified: {n_main} main (new/changed/reappeared), "
          f"{n_still} still active from yesterday")

    # 6b. Build map markers from all scored listings with coordinates
    map_markers = []
    if config.MAP_ENABLED:
        # Flatten all_scored (dict of deal_type -> [(listing, score, method)])
        # and attach scores to listings for map popups
        all_for_map = []
        for dt, items in all_scored.items():
            for item in items:
                listing = item[0]
                score = item[1]
                listing["_score"] = score
                all_for_map.append(listing)
        map_markers = geocode.get_map_data(all_for_map, price_data)
        print(f"[main] map: {len(map_markers)} markers with coordinates")

    # 7. Notify (pass price history + map markers)
    sent, info = notifier.send(main_deals, still_active, comparison_html,
                               status_note, price_data, map_markers)

    # 8. Build hosted site (latest digest -> docs/index.html + archive)
    website.build()

    # 9. Update state: record today's surfaced deals + save today's digest
    history.update_seen_deals(all_scored, seen_deals)
    history.save_last_digest(all_scored, today)

    msg = (f"Flat_Searcher run {today} complete: scraped {len(all_listings)}, "
           f"surfaced {n_main} new/changed + {n_still} still-active deals. "
           f"{info}. Scoring: {status_note}. Feedback or next steps?")
    _inject_chat(msg)
    return msg


if __name__ == "__main__":
    out = run()
    sys.exit(0)
