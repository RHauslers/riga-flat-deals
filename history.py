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
import json
import os
from datetime import date

import config


def _ensure_dirs():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.DIGEST_DIR, exist_ok=True)


def _listing_key(listing):
    return f"{listing['source']}:{listing['id']}"


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    _ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# seen_deals.json  (dict: "{source}:{id}" -> metadata)
# ---------------------------------------------------------------------------
def load_seen_deals():
    return _read_json(config.SEEN_DEALS_JSON, {})


def save_seen_deals(seen_deals):
    _write_json(config.SEEN_DEALS_JSON, seen_deals)


def update_seen_deals(scored_by_type, seen_deals):
    """Record today's surfaced deals into seen_deals, preserving first_shown_date."""
    today = date.today().isoformat()
    for dt, items in scored_by_type.items():
        for entry in items:
            listing, score, _method = entry
            key = _listing_key(listing)
            prev = seen_deals.get(key, {})
            seen_deals[key] = {
                "first_shown_date": prev.get("first_shown_date", today),
                "last_shown_date": today,
                "last_shown_price": _to_float(listing.get("price_eur")),
                "last_shown_score": float(score) if score is not None else None,
                "deal_type": listing.get("deal_type", dt),
            }
    save_seen_deals(seen_deals)


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# last_digest.json  (yesterday's top deals for comparison)
# ---------------------------------------------------------------------------
def load_last_digest():
    return _read_json(config.LAST_DIGEST_JSON, {})


def save_last_digest(scored_by_type, today):
    """Persist today's top deals so tomorrow's run can compare against them."""
    digest = {"date": today}
    for dt, items in scored_by_type.items():
        digest[dt] = []
        for entry in items:
            l, score, _method = entry
            digest[dt].append({
                "key": _listing_key(l),
                "score": float(score) if score is not None else None,
                "price": _to_float(l.get("price_eur")),
                "district": l.get("district", ""),
                "rooms": l.get("rooms"),
                "area_m2": l.get("area_m2"),
                "floor": l.get("floor", ""),
                "url": l.get("url", ""),
                "source": l.get("source", ""),
            })
    _write_json(config.LAST_DIGEST_JSON, digest)


# ---------------------------------------------------------------------------
# history.csv  (training data, unique listings by source:id)
# ---------------------------------------------------------------------------
def append_history(listings):
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
    # Track the latest price per source:id so we can detect changes
    latest_price = {}
    if exists:
        with open(config.HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                key = f"{r.get('source')}:{r.get('id')}"
                # DictReader yields rows in file order, so the last one wins
                try:
                    latest_price[key] = float(r.get('price_eur') or 0)
                except (ValueError, TypeError):
                    latest_price[key] = None
    today = date.today().isoformat()
    new_rows = 0
    with open(config.HISTORY_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=config.HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        for l in listings:
            key = _listing_key(l)
            current_price = l.get('price_eur')
            try:
                current_price_f = float(current_price) if current_price else None
            except (ValueError, TypeError):
                current_price_f = None
            prev = latest_price.get(key)
            if prev is not None and current_price_f is not None and prev == current_price_f:
                # Same price as last record — skip (no new info)
                continue
            # New listing OR price changed — append a row
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
