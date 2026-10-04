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
import config
import utils


def flat_active_rows(listings):
    """Compact snapshot of today's live flat ads: {k, p, d, s, u, r, a} =
    key, price, district, street (fallback title), url, rooms, area_m2.
    r/a feed the RELISTED match — a flat reposted under a new id keeps
    the same district+street+rooms and an almost identical area."""
    rows = []
    for l in listings or []:
        key = utils.listing_key(l)
        if not l.get("source") or not l.get("id"):
            continue
        rows.append({
            "k": key,
            "p": l.get("price_eur"),
            "d": l.get("district") or "",
            "s": l.get("street") or l.get("title") or "",
            "u": l.get("url") or "",
            "r": l.get("rooms"),
            "a": l.get("area_m2"),
        })
    return rows


def car_snapshot_rows(listings):
    """Same shape from car snapshot listings: {k, p, mk, mo, y, m, f, g, u}.
    m/f/g feed the car RELISTED match — a reposted car keeps its specs."""
    rows = []
    for l in listings or []:
        if not l.get("source") or not l.get("id"):
            continue
        rows.append({
            "k": utils.listing_key(l),
            "p": l.get("price_eur"),
            "mk": l.get("make") or "",
            "mo": l.get("model") or "",
            "y": l.get("year"),
            "m": l.get("mileage_km"),
            "f": l.get("fuel") or "",
            "g": l.get("gearbox") or "",
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


# ---------------------------------------------------------------------------
# RELISTED — ads that vanished and came back under a new ad id. Sellers who
# withdraw and repost (usually after a cut) are a motivated-seller signal.
# recent_gone survives inside flat_active.json for GONE_RELIST_DAYS days.
# ---------------------------------------------------------------------------

_RECENT_GONE_CAP = 400


def recent_gone_rows(prev_recent, gone_today, today, live_rows=None):
    """Updated persistent gone list for flat_active.json['recent_gone'].

    prev_recent: yesterday's stored list ({k,p,d,s,u,r,a,gone} dicts);
    entries older than GONE_RELIST_DAYS or whose key is live again are
    dropped. gone_today: today's gone_rows() output, stamped 'gone': today
    and deduped against what is already stored (a relist detected later
    rewrites the old entry's date — the LATEST disappearance is what
    matters). live_rows: today's snapshot — keys that came back are
    purged so a reactivated ad id is not reported as relisted."""
    live_keys = {r.get("k") for r in (live_rows or [])}
    kept = []
    for r in prev_recent or []:
        k = r.get("k")
        if not k or k in live_keys:
            continue
        if utils.days_since(r.get("gone")) is None or \
                utils.days_since(r.get("gone")) > config.GONE_RELIST_DAYS:
            continue
        kept.append(r)
    new_keys = {r.get("k") for r in kept}
    for r in gone_today or []:
        k = r.get("k")
        if not k or k in live_keys:
            continue
        if k in new_keys:
            for old in kept:
                if old.get("k") == k:
                    old["gone"] = today  # re-gone: freshest date wins
            continue
        row = dict(r)
        row["gone"] = today
        kept.append(row)
        new_keys.add(k)
    return kept[-_RECENT_GONE_CAP:]


def _norm_street(s):
    return " ".join(utils.strip_diacritics(s or "").lower().split())


def _matches_gone_flat(l, gone_row):
    """Live listing vs a recently-gone row: same physical flat under a
    different ad id. Identical district +
    normalised street + equal rooms, and — when both carry an area —
    within GONE_RELIST_AREA_DIFF_M2. When area is missing on either side
    the price must additionally be within 15% (weak-signal compensator).
    """
    if (l.get("district") or "") != (gone_row.get("d") or ""):
        return False
    ls, gs = l.get("street") or l.get("title"), gone_row.get("s")
    if not ls or not gs or _norm_street(ls) != _norm_street(gs):
        return False
    lr, gr = utils.to_int(l.get("rooms")), utils.to_int(gone_row.get("r"))
    if lr is None or gr is None or lr != gr:
        return False
    la, ga = utils.to_float(l.get("area_m2")), utils.to_float(gone_row.get("a"))
    if la is not None and ga is not None:
        return abs(la - ga) <= config.GONE_RELIST_AREA_DIFF_M2
    lp, gp = utils.to_float(l.get("price_eur")), utils.to_float(gone_row.get("p"))
    if lp is None or gp is None or gp <= 0:
        return False
    return abs(lp - gp) / gp <= 0.15


def find_relisted(listings, prev_active_keys, recent_gone):
    """{listing_key: gone_row} for live listings whose key was NOT live
    yesterday but which match a recently-gone ad — i.e. reposted flats."""
    out = {}
    for l in listings or []:
        key = utils.listing_key(l)
        if not key or key in (prev_active_keys or ()):
            continue
        for r in recent_gone or []:
            if _matches_gone_flat(l, r):
                out[key] = r
                break
    return out


def _norm_car(s):
    return utils.strip_diacritics(s or "").lower().replace("-", " ").strip()


def _matches_gone_car(l, gone_row):
    """Live listing vs a recently-gone row: same physical car reposted
    under a new ad id. Identical
    make+model+year+fuel (+gearbox when known), and mileage within 15% —
    reposters rarely edit the odometer figure much, while a different
    car of the same model almost never lands inside that window."""
    for lf, gf in (("make", "mk"), ("model", "mo"), ("fuel", "f")):
        if _norm_car(l.get(lf)) != _norm_car(gone_row.get(gf)):
            return False
    ly, gy = utils.to_int(l.get("year")), utils.to_int(gone_row.get("y"))
    if ly is None or gy is None or ly != gy:
        return False
    lg, gg = _norm_car(l.get("gearbox")), _norm_car(gone_row.get("g"))
    if lg and gg and lg != gg:
        return False
    lm, gm = utils.to_float(l.get("mileage_km")), utils.to_float(gone_row.get("m"))
    if lm is None or gm is None:
        # no odometer on either side -> require a close price instead
        lp, gp = utils.to_float(l.get("price_eur")), utils.to_float(gone_row.get("p"))
        if lp is None or gp is None or gp <= 0:
            return False
        return abs(lp - gp) / gp <= 0.15
    return abs(lm - gm) <= gm * 0.15 + 2000


def find_car_relisted(listings, prev_snapshot_keys, recent_gone):
    """{listing_key: gone_row} for today's car ads that were NOT live
    yesterday but match a recently-gone car — reposted under a new id."""
    out = {}
    for l in listings or []:
        key = utils.listing_key(l)
        if not key or key in (prev_snapshot_keys or ()):
            continue
        for r in recent_gone or []:
            if _matches_gone_car(l, r):
                out[key] = r
                break
    return out
