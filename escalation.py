# -*- coding: utf-8 -*-
"""
Hourly escalation scanner.

Runs every hour via GitHub Actions. Scrapes all listings, scores them, and
if any deal's score >= ESCALATION_SCORE_THRESHOLD AND it hasn't been alerted
yet, sends an instant alert email with the link.

This is SEPARATE from the daily digest:
  - The daily digest (main.py) sends a full ranked digest once a day.
  - The hourly scan (escalation.py) sends instant alerts only for exceptional
    deals (score >= threshold), and only once per listing (tracked in
    alerted_deals.json).

The hourly scan:
  1. Scrapes ss.com + city24.lv (same as daily).
  2. Appends new listings to history.csv (model keeps learning).
  3. Scores ALL current listings against prior history.
  4. Filters to deals scoring >= threshold that haven't been alerted yet.
  5. If any -> sends alert email + marks them as alerted.
  6. If none -> silent (no email, no output beyond a log line).
  7. Builds website if there are hot deals (so the site shows them immediately).

Run: python -m escalation
"""
import sys
import traceback
from datetime import date, datetime

import config
import history
import scoring
import notifier
import website
import health
import utils
import price_history
import geocode
from scrapers import ss_com, city24


def _listing_key(l):
    return f"{l.get('source')}:{l.get('id')}"


def _inject_chat(message):
    if not config.CHAT_INJECT_ENABLED:
        return
    try:
        import pyperclip
        pyperclip.copy(message)
        print(f"\n[chat] {message}\n")
    except Exception:
        pass


def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Flat_Searcher hourly escalation scan {now} ===")

    if not config.ESCALATION_ENABLED:
        print("[escalation] disabled in config — skipping")
        return "escalation disabled"

    # 1. Scrape (tracking per-source counts for health checks)
    #    Use fewer pages for SS.com to limit request volume (hourly runs
    #    would otherwise hit ss.com ~2000x/day and risk an IP block).
    all_listings = []
    source_counts = {"ss.com": 0, "city24.lv": 0}
    for dt in config.DEAL_TYPES:
        for scraper in (ss_com, city24):
            try:
                if scraper is ss_com:
                    items = scraper.scrape(dt, max_pages=config.SS_COM_MAX_PAGES_HOURLY)
                else:
                    items = scraper.scrape(dt)
                all_listings.extend(items)
                for it in items:
                    src = it.get("source")
                    if src in source_counts:
                        source_counts[src] += 1
            except Exception as e:
                print(f"[escalation] {scraper.__name__} {dt} failed: {e}")
                traceback.print_exc()

    print(f"[escalation] total scraped (target districts): {len(all_listings)} "
          f"per source: {source_counts}")

    # 2. Sanity filter (same as main.py)
    min_price = {"sale": config.MIN_SALE_PRICE_EUR, "rent": config.MIN_RENT_PRICE_EUR}
    before = len(all_listings)
    all_listings = [l for l in all_listings
                    if l.get("price_eur") and l["price_eur"] >= min_price.get(l.get("deal_type"), 0)]
    dropped = before - len(all_listings)
    if dropped:
        print(f"[escalation] dropped {dropped} listing(s) with implausible prices")

    # 2b. Merge the same flat listed on multiple portals
    all_listings, _n_merged = utils.dedupe_cross_source(all_listings)

    # 2c. Health check -> alerts the OPERATOR if a scraper looks broken
    health.check_and_alert(source_counts, len(all_listings), context="hourly")

    if not all_listings:
        print("[escalation] no listings found — nothing to scan")
        return "no listings"

    # 3. Training baseline = everything BEFORE today (see history.load_history).
    #    Must match main.py so the daily digest and hourly scan agree, and so
    #    repeated hourly appends cannot poison the baseline.
    hist_rows = history.load_history(exclude_today=True)
    history.append_history(all_listings)

    # 3b. Update price history (CenuMednieks + our own tracking) so hot-deal
    #     alert emails include price drop context and days on market.
    price_data = price_history.update_price_history(all_listings)

    # 4. Score ALL current listings
    all_scored = scoring.score_and_rank(all_listings, hist_rows)

    # 4b. Minimum-history gate. A z-score derived from a handful of rows is
    #     noise, not signal - without this the very first days could fire a
    #     "HOT DEAL" email off 7 data points. Gate per deal type.
    hist_counts = {}
    for r in hist_rows:
        dt_key = r.get("deal_type")
        hist_counts[dt_key] = hist_counts.get(dt_key, 0) + 1

    eligible = {}
    for dt in all_scored:
        n = hist_counts.get(dt, 0)
        if n >= config.ESCALATION_MIN_HISTORY:
            eligible[dt] = True
        else:
            eligible[dt] = False
            print(f"[escalation] {dt}: only {n} history rows "
                  f"(need {config.ESCALATION_MIN_HISTORY}) - scores not yet "
                  f"trustworthy, escalation suppressed")

    # 5. Flatten all scored deals and filter to hot + un-alerted
    threshold = config.ESCALATION_SCORE_THRESHOLD
    hot_deals = []
    hot_keys = []
    for dt, items in all_scored.items():
        if not eligible.get(dt):
            continue
        for listing, score, method in items:
            if score is not None and score >= threshold:
                key = _listing_key(listing)
                if not history.is_alerted(key):
                    hot_deals.append((listing, score, method))
                    hot_keys.append(key)

    if not hot_deals:
        print(f"[escalation] no deals scored >= {threshold} — no alert sent")
        msg = (f"Hourly scan {now}: scraped {len(all_listings)}, "
               f"no deals above threshold {threshold}. No alert sent.")
        _inject_chat(msg)
        return msg

    # 6. Send alert email
    print(f"[escalation] {len(hot_deals)} deal(s) scored >= {threshold} — sending alert!")
    for l, s, m in hot_deals:
        print(f"  -> score {s:+.2f} | {l.get('district')} | {l.get('rooms')}r "
              f"{l.get('area_m2')}m2 | {_fmt_price_inline(l.get('price_eur'))} | {l.get('url')}")

    sent, info = notifier.send_alert(hot_deals, threshold, price_data)

    # 7. Mark as alerted so we don't re-alert next hour
    history.mark_alerted(hot_keys)

    # 8. Update website so the site shows the hot deal immediately
    website.build()

    msg = (f"Hourly scan {now}: scraped {len(all_listings)}, "
           f"{len(hot_deals)} HOT deal(s) above threshold {threshold}! "
           f"{info}. Feedback or next steps?")
    _inject_chat(msg)
    return msg


def _fmt_price_inline(v):
    try:
        return f"{float(v):,.0f} EUR".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


if __name__ == "__main__":
    out = run()
    sys.exit(0)
