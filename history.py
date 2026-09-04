# -*- coding: utf-8 -*-
"""
Persistent storage:
  - history.csv      : every listing ever scraped (training data for the model)
  - seen_deals.json  : dict keyed by "{source}:{id}" with
                       {first_shown_date, last_shown_date, last_shown_price,
                        last_shown_score, deal_type}
  - last_digest.json : yesterday's top deals (for "vs yesterday" comparison)
  - unsubscribed.json: list of recipient emails that opted out

The data/ folder is committed back to the repo by GitHub Actions so state
persists across daily runs.

Legacy: seen_ids.json (flat set) is migrated to seen_deals.json on first run.
"""
import csv
import json
import os
from datetime import date, datetime, timedelta

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
# unsubscribed.json
# ---------------------------------------------------------------------------
def load_unsubscribed():
    return _read_json(config.UNSUBSCRIBED_JSON, [])


def is_unsubscribed(email):
    if not email:
        return False
    email = email.strip().lower()
    unsubscribed = load_unsubscribed()
    return email in [e.strip().lower() for e in unsubscribed if isinstance(e, str)]


# ---------------------------------------------------------------------------
# alerted_deals.json  (deals already sent as hourly escalation alerts)
# ---------------------------------------------------------------------------
def load_alerted_deals():
    return _read_json(config.ALERTED_DEALS_JSON, [])


def is_alerted(key):
    return key in set(load_alerted_deals())


def mark_alerted(keys):
    """Add keys to alerted_deals.json and persist."""
    alerted = load_alerted_deals()
    alerted_set = set(alerted)
    changed = False
    for k in keys:
        if k not in alerted_set:
            alerted.append(k)
            alerted_set.add(k)
            changed = True
    if changed:
        _write_json(config.ALERTED_DEALS_JSON, alerted)
    return changed


# ---------------------------------------------------------------------------
# ops_alerts.json  (throttle operator health alerts to once per issue per day)
# ---------------------------------------------------------------------------
def should_send_ops_alert(issue_key):
    """True if this issue hasn't already been alerted today."""
    today = date.today().isoformat()
    sent = _read_json(config.OPS_ALERTS_JSON, {})
    return sent.get(issue_key) != today


def mark_ops_alert_sent(issue_key):
    today = date.today().isoformat()
    sent = _read_json(config.OPS_ALERTS_JSON, {})
    sent[issue_key] = today
    _write_json(config.OPS_ALERTS_JSON, sent)


# ---------------------------------------------------------------------------
# Migration: seen_ids.json (legacy flat set) -> seen_deals.json (dict)
# ---------------------------------------------------------------------------
def migrate_seen_ids():
    """One-time migration from the legacy flat-set seen_ids.json to seen_deals.json.
    Enriches first/last shown price from history.csv where possible."""
    if os.path.exists(config.SEEN_DEALS_JSON):
        return  # already migrated
    legacy = _read_json(config.SEEN_IDS_JSON, None)
    if not legacy:
        return
    seen_deals = {}
    # build a lookup of first-seen price/score from history
    hist_prices = {}
    if os.path.exists(config.HISTORY_CSV):
        with open(config.HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                k = f"{r.get('source')}:{r.get('id')}"
                hist_prices[k] = {
                    "price": _to_float(r.get("price_eur")),
                    "deal_type": r.get("deal_type", ""),
                    "scrape_date": r.get("scrape_date", ""),
                }
    for key in legacy:
        hp = hist_prices.get(key, {})
        seen_deals[key] = {
            "first_shown_date": hp.get("scrape_date") or "unknown",
            "last_shown_date": hp.get("scrape_date") or "unknown",
            "last_shown_price": hp.get("price"),
            "last_shown_score": None,
            "deal_type": hp.get("deal_type", ""),
        }
    save_seen_deals(seen_deals)
    print(f"[history] migrated {len(seen_deals)} entries from seen_ids.json -> seen_deals.json")


# ---------------------------------------------------------------------------
# history.csv  (training data, unique listings by source:id)
# ---------------------------------------------------------------------------
def append_history(listings):
    """Append unified listings to history.csv, skipping rows already present
    (matched by source:id). This keeps history = set of unique listings ever
    seen, avoiding bloat from same-day re-runs."""
    if not listings:
        return
    _ensure_dirs()
    exists = os.path.exists(config.HISTORY_CSV)
    existing_keys = set()
    if exists:
        with open(config.HISTORY_CSV, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                existing_keys.add(f"{r.get('source')}:{r.get('id')}")
    today = date.today().isoformat()
    new_rows = 0
    with open(config.HISTORY_CSV, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=config.HISTORY_COLUMNS)
        if not exists:
            writer.writeheader()
        for l in listings:
            key = _listing_key(l)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            row = {"scrape_date": today}
            for col in config.HISTORY_COLUMNS:
                if col == "scrape_date":
                    continue
                row[col] = l.get(col, "")
            writer.writerow(row)
            new_rows += 1
    if new_rows:
        print(f"[history] appended {new_rows} new unique rows")


def load_history(exclude_today=False):
    """Return list of dicts (full history).

    exclude_today=True drops rows scraped today. Use this for MODEL TRAINING:
    both the daily digest and the hourly escalation scan append today's
    listings to history.csv, so "everything before today" is the only baseline
    that is independent of which job ran first. Without this, the hourly scan
    (running at :05) would poison the 10:00 daily digest's baseline by having
    already inserted today's listings - a listing would help define the average
    it is judged against, making genuine bargains look ordinary.
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


# ---------------------------------------------------------------------------
# Backward-compat wrappers (used by any code not yet upgraded)
# ---------------------------------------------------------------------------
def load_seen():
    """Legacy: return a set of '{source}:{id}' already emailed.
    Now derived from seen_deals.json keys."""
    return set(load_seen_deals().keys())


def save_seen(seen):
    """Legacy best-effort: no-op if seen_deals.json already exists (don't
    overwrite the richer dict with a flat set)."""
    if os.path.exists(config.SEEN_DEALS_JSON):
        return
    today = date.today().isoformat()
    seen_deals = {
        k: {
            "first_shown_date": today,
            "last_shown_date": today,
            "last_shown_price": None,
            "last_shown_score": None,
            "deal_type": "",
        }
        for k in seen
    }
    save_seen_deals(seen_deals)


def mark_seen(listings, seen):
    """Legacy best-effort wrapper. Prefer update_seen_deals."""
    today = date.today().isoformat()
    sd = load_seen_deals()
    for l in listings:
        key = _listing_key(l)
        prev = sd.get(key, {})
        sd[key] = {
            "first_shown_date": prev.get("first_shown_date", today),
            "last_shown_date": today,
            "last_shown_price": _to_float(l.get("price_eur")),
            "last_shown_score": None,
            "deal_type": l.get("deal_type", ""),
        }
    save_seen_deals(sd)


def filter_new(listings, seen):
    """Return only listings whose key is not in seen (set or dict keys)."""
    if isinstance(seen, dict):
        seen_keys = set(seen.keys())
    else:
        seen_keys = set(seen)
    return [l for l in listings if _listing_key(l) not in seen_keys]
