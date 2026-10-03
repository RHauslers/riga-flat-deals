# -*- coding: utf-8 -*-
"""'Gone since yesterday' tracking — flats and cars.

Each daily run snapshots today's live ads (data/flat_active.json for
flats, data/car_market_snapshot.json for cars). Comparing yesterday's
snapshot against today's ids yields the ads that disappeared overnight —
usually sold, sometimes expired or withdrawn. That is exactly the set a
buyer cares about: watchlist candidates they can stop tracking, and a
signal of how fast the market actually moves.

Rows are only reported for sources that produced data today — a scraper
returning nothing might just have failed, and calling every one of its
ads 'gone' would be noise (or wrong).
"""


def listing_key(l):
    return f"{l.get('source')}:{l.get('id')}"


def flat_active_rows(listings):
    """Compact snapshot of today's live flat ads: {k, p, d, s, u} =
    key, price, district, street (fallback title), url."""
    rows = []
    for l in listings or []:
        key = listing_key(l)
        if not l.get("source") or not l.get("id"):
            continue
        rows.append({
            "k": key,
            "p": l.get("price_eur"),
            "d": l.get("district") or "",
            "s": l.get("street") or l.get("title") or "",
            "u": l.get("url") or "",
        })
    return rows


def car_snapshot_rows(listings):
    """Same shape from car snapshot listings: {k, p, mk, mo, y, u}."""
    rows = []
    for l in listings or []:
        if not l.get("source") or not l.get("id"):
            continue
        rows.append({
            "k": listing_key(l),
            "p": l.get("price_eur"),
            "mk": l.get("make") or "",
            "mo": l.get("model") or "",
            "y": l.get("year"),
            "u": l.get("url") or "",
        })
    return rows


def gone_rows(prev_rows, today_keys, ok_sources, max_rows):
    """Yesterday's rows whose key is absent from today's scan.

    ``ok_sources`` = source names that produced at least one listing
    today — ads from silent sources are never reported as gone.
    Oldest-snapshot order is preserved (the snapshot is stable, so the
    output is deterministic); capped at max_rows."""
    if not prev_rows or not ok_sources:
        return []
    out = []
    for r in prev_rows:
        key = r.get("k") or ""
        if not key or key in today_keys:
            continue
        if key.split(":", 1)[0] not in ok_sources:
            continue
        out.append(r)
        if len(out) >= max_rows:
            break
    return out
