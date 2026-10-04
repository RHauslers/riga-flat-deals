# -*- coding: utf-8 -*-
"""
Persistent storage:
  - history.csv      : every listing ever scraped (training data for the model)
  - seen_deals.json  : dict keyed by "{source}:{id}" with
                       {first_shown_date, last_shown_date, last_shown_price,
                        last_shown_score, deal_type}
  - last_digest.json : yesterday's top deals (for "vs yesterday" comparison)

The data/ folder is committed back to the repo by GitHub Actions so state
persists across daily runs.
"""
import csv
import os
from datetime import date, timedelta

import config
import utils


def _ensure_dirs():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.DIGEST_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# seen_deals.json  (dict: "{source}:{id}" -> metadata)
# ---------------------------------------------------------------------------
def load_seen_deals():
    return utils.read_json(config.SEEN_DEALS_JSON, {})


def save_seen_deals(seen_deals):
    # Compact JSON — these files are committed daily, pretty-printing was
    # adding ~35% of dead whitespace (same reason car state went v2).
    utils.write_json(config.SEEN_DEALS_JSON, seen_deals, indent=None)


def update_seen_deals(scored_by_type, seen_deals):
    """Record today's surfaced deals into seen_deals, preserving first_shown_date."""
    today = date.today().isoformat()
    for dt, items in scored_by_type.items():
        for entry in items:
            listing, score, _method = entry
            key = utils.listing_key(listing)
            prev = seen_deals.get(key, {})
            seen_deals[key] = {
                "first_shown_date": prev.get("first_shown_date", today),
                "last_shown_date": today,
                "last_shown_price": utils.to_float(listing.get("price_eur")),
                "last_shown_score": float(score) if score is not None else None,
                "deal_type": listing.get("deal_type", dt),
            }
    # Prune entries not re-shown for SEEN_DEALS_TTL_DAYS — the dict
    # otherwise grows forever with long-gone listings.
    cutoff = (date.today()
              - timedelta(days=config.SEEN_DEALS_TTL_DAYS)).isoformat()
    stale = [k for k, v in seen_deals.items()
             if (v.get("last_shown_date") or "") < cutoff]
    for k in stale:
        del seen_deals[k]
    if stale:
        print(f"[history] pruned {len(stale)} seen_deals entries "
              f"(>{config.SEEN_DEALS_TTL_DAYS}d not shown)")
    save_seen_deals(seen_deals)


# ---------------------------------------------------------------------------
# last_digest.json  (yesterday's top deals for comparison)
# ---------------------------------------------------------------------------
def load_last_digest():
    return utils.read_json(config.LAST_DIGEST_JSON, {})


def save_last_digest(scored_by_type, today):
    """Persist today's top deals so tomorrow's run can compare against them."""
    digest = {"date": today}
    for dt, items in scored_by_type.items():
        digest[dt] = []
        for entry in items:
            l, score, _method = entry
            digest[dt].append({
                "key": utils.listing_key(l),
                "score": float(score) if score is not None else None,
                "price": utils.to_float(l.get("price_eur")),
                "district": l.get("district", ""),
                "rooms": l.get("rooms"),
                "area_m2": l.get("area_m2"),
                "floor": l.get("floor", ""),
                "url": l.get("url", ""),
                "source": l.get("source", ""),
            })
    utils.write_json(config.LAST_DIGEST_JSON, digest, indent=None)


# ---------------------------------------------------------------------------
# history.csv  (training data, unique listings by source:id)
# ---------------------------------------------------------------------------
def latest_prices(rows):
    """{key: latest_price} across already-loaded history rows — caller
    supplies load_history() output so append_history doesn't rescan the
    file (it used to re-read the whole CSV just for this map)."""
    latest = {}
    for r in rows:
        latest[utils.listing_key(r)] = utils.to_float(r.get('price_eur'))
    return latest


def append_history(listings, latest_price=None):
    """Append unified listings to history.csv.

    A listing is appended when:
      - it has never been seen before (new source:id), OR
      - its price has changed since the last recorded row for that source:id.

    This means price drops/increases are captured as new training rows so the
    regression model learns from current prices, not stale ones. Same-day
    re-runs with identical prices are still skipped (no bloat).
    """
    if not listings:
        return
    _ensure_dirs()
    exists = os.path.exists(config.HISTORY_CSV)
    if latest_price is None:
        # Standalone path — scan the file ourselves for the latest price
        # per key (the pipeline passes latest_prices(load_history()) and
        # skips this second read entirely).
        latest_price = {}
        if exists:
            with open(config.HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    # DictReader yields rows in file order, so the last one wins
                    latest_price[utils.listing_key(r)] = utils.to_float(r.get('price_eur'))
    today = date.today().isoformat()
    new_rows = 0
    with open(config.HISTORY_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=config.HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        for l in listings:
            key = utils.listing_key(l)
            current_price = l.get('price_eur')
            try:
                current_price_f = float(current_price) if current_price else None
            except (ValueError, TypeError):
                current_price_f = None
            prev = latest_price.get(key)
            if prev is not None and current_price_f is not None and prev == current_price_f:
                # Same price as last record — skip (no new info)
                continue
            # New listing OR price changed — append a row. Only record a
            # real price: writing None would erase the dedupe baseline so
            # tomorrow's run re-appends the same unchanged listing.
            if current_price_f is not None:
                latest_price[key] = current_price_f
            row = {"scrape_date": today}
            for col in config.HISTORY_COLUMNS:
                if col == "scrape_date":
                    continue
                row[col] = l.get(col, "")
            writer.writerow(row)
            new_rows += 1
    if new_rows:
        print(f"[history] appended {new_rows} new/changed rows")


def load_history(exclude_today=False):
    """Return list of dicts (full history).

    exclude_today=True drops rows scraped today. Use this for MODEL TRAINING:
    "everything before today" is a baseline independent of how many times the
    pipeline ran today (manual reruns append too). Without it a listing would
    help define the average it is judged against, making genuine bargains
    look ordinary.
    """
    if not os.path.exists(config.HISTORY_CSV):
        return []
    today = date.today().isoformat()
    rows = []
    with open(config.HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if exclude_today and r.get("scrape_date") == today:
                continue
            rows.append(r)
    return rows
