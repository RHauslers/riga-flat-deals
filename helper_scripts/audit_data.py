# -*- coding: utf-8 -*-
"""audit_data.py — one-shot health report over data/ state files.

Prints, per state file: size, entry counts, freshness (vs today), and a
warnings section at the end flagging anything anomalous — stale snapshots,
missing files, oversized state, geocode misses, dead price-history entries.
Read-only; never writes. Handy at the start of a session or when debugging
"why does the site look weird".

    python -X utf8 helper_scripts/audit_data.py
"""
import csv
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config   # noqa: E402

TODAY = date.today().isoformat()
SEEN_EPOCH = date(2026, 1, 1)          # cars.py v2 epoch-day base
GEO_MISS_STALE_DAYS = 7                # geocode.py retry window for misses
WARNINGS = []


def _warn(msg):
    WARNINGS.append(msg)
    print(f"  !! {msg}")


def _kb(path):
    try:
        return os.path.getsize(path) / 1024.0
    except OSError:
        return 0.0


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _report(name, path):
    if not os.path.exists(path):
        print(f"{name:32} MISSING")
        _warn(f"{name} is missing ({path})")
        return None
    print(f"{name:32} {_kb(path):8.0f} KB", end="  ")
    return _load(path, None)


def _age(iso):
    try:
        return (date.today() - date.fromisoformat(str(iso))).days
    except (ValueError, TypeError):
        return None


def _stale_flag(iso, what):
    a = _age(iso)
    if a is None:
        _warn(f"{what}: unparseable date {iso!r}")
        return "?"
    if a > 1:
        _warn(f"{what} is {a}d stale (date={iso})")
        return f"{a}d STALE"
    return "fresh"


def _seen_day_to_iso(n):
    try:
        return (SEEN_EPOCH + timedelta(days=int(n))).isoformat()
    except (TypeError, ValueError):
        return None


def main():
    print(f"=== data/ audit — {TODAY} ===\n")

    # history.csv — training baseline
    path = config.HISTORY_CSV
    if os.path.exists(path):
        days = set()
        n_rows = 0
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                n_rows += 1
                if r.get("scrape_date"):
                    days.add(r["scrape_date"])
        print(f"{'history.csv':32} {_kb(path):8.0f} KB  "
              f"{n_rows} rows, {len(days)} days "
              f"({min(days) if days else '-'} .. {max(days) if days else '-'})")
        _stale_flag(max(days) if days else None, "history.csv last row")
    else:
        _report("history.csv", path)

    # .main.lock — a leftover means a run is in progress or crashed
    lock = config.MAIN_LOCK_FILE
    if os.path.exists(lock):
        info = _load(lock, {}) or {}
        try:
            age_h = (time.time() - float(
                info.get("ts") or os.path.getmtime(lock))) / 3600
        except (TypeError, ValueError, OSError):
            age_h = None
        if age_h is None or age_h >= config.MAIN_LOCK_STALE_HOURS:
            state = "STALE (crashed run)"
            _warn("stale .main.lock — a run crashed; it is taken over "
                  "automatically, or delete it manually")
        else:
            state = "run in progress?"
        age_txt = f"{age_h:.1f}h old" if age_h is not None else "unreadable"
        print(f"{'.main.lock':32} {'':8}  pid={info.get('pid')}, "
              f"{info.get('date')}, {age_txt} -> {state}")

    # seen_deals.json
    d = _report("seen_deals.json", config.SEEN_DEALS_JSON)
    if isinstance(d, dict):
        old = sum(1 for v in d.values()
                  if (_age((v or {}).get("last_shown_date")) or 0)
                  > config.SEEN_DEALS_TTL_DAYS)
        print(f"{len(d)} entries, {old} older than "
              f"{config.SEEN_DEALS_TTL_DAYS}d (pruned next run)")

    # last_digest.json
    d = _report("last_digest.json", config.LAST_DIGEST_JSON)
    if isinstance(d, dict):
        counts = {k: len(v) for k, v in d.items()
                  if isinstance(v, list)}
        print(f"date={d.get('date')} {counts} "
              f"[{_stale_flag(d.get('date'), 'last_digest')}]")

    # flat_active.json (gone-tracking snapshot)
    d = _report("flat_active.json", config.FLAT_ACTIVE_JSON)
    if isinstance(d, dict):
        print(f"date={d.get('date')} rows={len(d.get('rows') or [])} "
              f"[{_stale_flag(d.get('date'), 'flat_active')}]")

    # price_history.json
    d = _report("price_history.json", config.PRICE_HISTORY_JSON)
    if isinstance(d, dict):
        n_cenu = sum(1 for e in d.values() if (e or {}).get("cenumednieks"))
        oldest = min(
            (str(o.get("date")) for e in d.values()
             for o in (e or {}).get("our_tracking") or []
             if o.get("date")),
            default=None)
        dead = sum(1 for e in d.values()
                   if (_age(_last_activity(e)) or 0)
                   > config.PRICE_HISTORY_KEEP_DAYS)
        legacy = sum(1 for e in d.values()
                     if e.get("cenumednieks") is None
                     and not e.get("cenumednieks_attempt"))
        print(f"{len(d)} entries, {n_cenu} with CenuMednieks, "
              f"oldest obs {oldest or '-'}, {dead} prunable, "
              f"{legacy} never-attempted (fetch once next run)")

    # geocode_cache.json
    d = _report("geocode_cache.json", config.GEOCODE_CACHE_JSON)
    if isinstance(d, dict):
        misses = sum(1 for e in d.values() if (e or {}).get("lat") is None)
        pct = 100.0 * misses / len(d) if d else 0.0
        print(f"{len(d)} entries, {misses} misses ({pct:.0f}%)")
        if d and pct > 15:
            _warn(f"geocode miss ratio {pct:.0f}% is high — check the "
                  f"address normalization")

    # car_seen.json (v2 epoch-day format)
    d = _report("car_seen.json", config.CAR_SEEN_JSON)
    if isinstance(d, dict):
        entries = d.get("entries") or {}
        stale = sum(1 for e in entries.values()
                    if (_age(_seen_day_to_iso((e or {}).get("ls"))) or 0)
                    > config.CAR_SEEN_TTL_DAYS)
        print(f"v{d.get('v')}, {len(entries)} entries, "
              f"{stale} older than {config.CAR_SEEN_TTL_DAYS}d (pruned on "
              f"next eligible run)")

    # car_market_snapshot.json (v2 columnar)
    d = _report("car_market_snapshot.json", config.CAR_MARKET_SNAPSHOT_JSON)
    if isinstance(d, dict):
        print(f"v{d.get('v')}, date={d.get('date')}, "
              f"{len(d.get('rows') or [])} rows "
              f"[{_stale_flag(d.get('date'), 'car snapshot')}]")

    # car_market_stats.json / flat_market_stats.json
    for name, path in (("car_market_stats.json", config.CAR_MARKET_STATS_JSON),
                       ("flat_market_stats.json", config.FLAT_MARKET_STATS_JSON)):
        d = _report(name, path)
        if isinstance(d, dict):
            n = len(d.get("models") or d.get("districts") or [])
            print(f"date={d.get('date')}, total={d.get('total')}, "
                  f"{n} groups [{_stale_flag(d.get('date'), name)}]")

    # market histories
    for name, path in (("car_market_history.json",
                        config.CAR_MARKET_HISTORY_JSON),
                       ("flat_market_history.json",
                        config.FLAT_MARKET_HISTORY_JSON)):
        d = _report(name, path)
        if isinstance(d, dict):
            n_pts = sum(len(v) for v in d.values())
            print(f"{len(d)} series, {n_pts} points")
            bad = {k for k, pts in d.items()
                   if [str(p[0]) for p in pts]
                   != sorted(str(p[0]) for p in pts)
                   or len({str(p[0]) for p in pts}) != len(pts)}
            if bad:
                _warn(f"{name}: unsorted/duplicate dates in series: "
                      f"{sorted(bad)[:5]}")

    # car_model_scans.json — deep-scan rotation state
    d = _report("car_model_scans.json", config.CAR_MODEL_SCAN_JSON)
    if isinstance(d, dict):
        ages = [_age(v) for v in d.values()]
        ages = [a for a in ages if a is not None]
        print(f"{len(d)} models, scans "
              f"{min(ages) if ages else '-'}..{max(ages) if ages else '-'}d old")

    # digests dir
    dd = config.DIGEST_DIR
    if os.path.isdir(dd):
        files = sorted(f for f in os.listdir(dd) if f.endswith(".html"))
        flat = [f for f in files if f.startswith("digest_")]
        car = [f for f in files if f.startswith("cars_")]
        print(f"{'digests/':32} {'':8}  {len(flat)} flat + {len(car)} car; "
              f"newest flat={flat[-1] if flat else '-'}, "
              f"car={car[-1] if car else '-'}")
        _stale_flag((flat[-1][7:17] if flat else None), "newest flat digest")
        _stale_flag((car[-1][5:15] if car else None), "newest car digest")
        # the live digests must still carry their market embeds — the
        # browser budget/watch tools break silently without them
        # (archives are the ones supposed to lose the embed)
        for fname, embed_id in ((flat[-1] if flat else None,
                                 'id="flat-listings-data"'),
                                (car[-1] if car else None,
                                 'id="car-market-data"')):
            if not fname:
                continue
            try:
                with open(os.path.join(dd, fname),
                          encoding="utf-8") as fh:
                    if embed_id not in fh.read():
                        _warn(f"{fname} is missing the {embed_id} embed "
                              f"— the browser budget tool shows nothing")
            except OSError:
                pass

    # embed/JS field parity — every idx('field') lookup in the budget
    # JS must exist in the embedded fields tuple, or the browser tool
    # silently reads undefined for it (fields are positional)
    try:
        import re

        import car_digest
        import notifier
        for js_src, fields, label in (
                (notifier.FLAT_BUDGET_JS, notifier._FLAT_FIELDS, "flat"),
                (car_digest.CAR_BUDGET_JS, car_digest._MARKET_FIELDS,
                 "car")):
            used = {a or b for a, b in re.findall(
                r"""idx\.(\w+)|idx\[['"](\w+)['"]\]""", js_src)}
            missing = used - set(fields)
            if missing:
                _warn(f"{label} budget JS looks up fields missing from "
                      f"the embed: {sorted(missing)}")
            else:
                print(f"  {label} embed fields: {len(fields)} embedded, "
                      f"{len(used)} used by JS — parity ok")
    except Exception as ex:  # audit must never break the daily run
        print(f"  embed parity check failed to run: {ex}")

    # Cross-file freshness: each side's gone-tracking snapshot is written
    # in the same run that writes its digest, so their dates should match.
    # A mismatch means a partial run — state committed but the digest
    # failed, or the digest rendered while state stayed behind.
    try:
        dd = config.DIGEST_DIR
        files = sorted(os.listdir(dd)) if os.path.isdir(dd) else []
        pairs = (
            ("flat", (_load(config.FLAT_ACTIVE_JSON, {}) or {}).get("date"),
             max((f[7:17] for f in files if f.startswith("digest_")),
                 default=None)),
            ("car", (_load(config.CAR_MARKET_SNAPSHOT_JSON, {}) or {})
             .get("date"),
             max((f[5:15] for f in files if f.startswith("cars_")),
                 default=None)),
        )
        for name, state_d, digest_d in pairs:
            if state_d and digest_d and state_d != digest_d:
                _warn(f"{name} snapshot date {state_d} != newest digest "
                      f"{digest_d} — yesterday's run finished partway "
                      f"(gone/relisted detection may be off by a day)")
    except OSError:
        pass

    print(f"\n=== {len(WARNINGS)} warning(s) ===")
    for w in WARNINGS:
        print(f" - {w}")


def _last_activity(entry):
    dates = [str(o.get("date")) for o in (entry or {}).get("our_tracking") or []
             if o.get("date")]
    cenu = (entry or {}).get("cenumednieks") or {}
    if cenu.get("fetched_at"):
        dates.append(str(cenu["fetched_at"]))
    if (entry or {}).get("first_seen"):
        dates.append(str(entry["first_seen"]))
    return max(dates) if dates else None


if __name__ == "__main__":
    main()
