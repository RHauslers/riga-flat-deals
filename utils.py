# -*- coding: utf-8 -*-
"""Shared helpers: diacritic stripping, district matching, slugify,
plus the small IO/format/stat helpers several modules used to carry
private copies of."""
import json
import os
import re
import statistics
import unicodedata
from datetime import date, datetime, timedelta, timezone
from html import escape as _html_escape

import config


# ---------------------------------------------------------------------------
# JSON IO (was duplicated in cars.py, history.py, geocode.py, price_history.py)
# ---------------------------------------------------------------------------
def read_json(path, default):
    """json.load with tolerant defaults: missing/corrupt file -> default."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path, data, indent=2):
    """Write JSON (UTF-8, non-ASCII preserved). indent=None -> compact.

    Writes to a sibling .tmp file first, then os.replace()s it into place
    — a kill/crash/disk-full mid-write then leaves the previous good file
    untouched instead of a truncated JSON every reader chokes on.
    os.replace is atomic on POSIX and on Windows (MoveFileExW) as long as
    source and target share a filesystem, which a sibling path guarantees."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            if indent is None:
                json.dump(data, f, ensure_ascii=False,
                          separators=(",", ":"))
            else:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_text(path, text):
    """Atomic UTF-8 text write — same tmp+os.replace discipline as
    write_json, for the HTML digests: a crash mid-write must not leave a
    truncated page for website.build() to publish."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Listing identity — every module used to build "source:id" keys inline
# ---------------------------------------------------------------------------
def listing_key(listing):
    """Canonical 'source:id' key for a listing dict ('' for missing parts)."""
    return f"{listing.get('source') or ''}:{listing.get('id') or ''}"


# ---------------------------------------------------------------------------
# Number coercion (was duplicated as _to_float/_safe_float/_number everywhere)
# ---------------------------------------------------------------------------
def to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def esc(v):
    """HTML-escape any value for embedding in digest markup."""
    return _html_escape(str(v if v is not None else ""), quote=True)


def fmt_eur(v):
    """'€12,345' or '—' for missing values."""
    if v is None:
        return "—"
    try:
        return "€{:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


def fmt_num(v):
    """'12,345' or '—'."""
    if v is None:
        return "—"
    try:
        return "{:,}".format(int(round(float(v))))
    except (TypeError, ValueError):
        return "—"


def median(values):
    """statistics.median over the non-None values, or None."""
    vals = [float(v) for v in values if v is not None]
    try:
        return statistics.median(vals) if vals else None
    except (TypeError, ValueError):
        return None


def riga_now_str():
    """'YYYY-MM-DD HH:MM (Riga time)' for digest headers — Europe/Riga when
    tzdata is available, UTC labelled as such otherwise. Was duplicated
    (with different fallbacks) in notifier._now_header_str and
    car_digest._riga_stamp."""
    try:
        from zoneinfo import ZoneInfo
        return (datetime.now(ZoneInfo("Europe/Riga"))
                .strftime("%Y-%m-%d %H:%M") + " (Riga time)")
    except Exception:
        return (datetime.now(timezone.utc)
                .strftime("%Y-%m-%d %H:%M") + " (UTC)")


def days_since(date_str, today=None):
    """Days between today and an ISO date string, or None if unparseable."""
    if not date_str or date_str == "unknown":
        return None
    try:
        d = datetime.fromisoformat(str(date_str)).date()
    except (ValueError, TypeError):
        return None
    today = today or date.today()
    return (today - d).days


# ---------------------------------------------------------------------------
# Motivated-seller detection — real asking-price drop + staleness or
# repeated cutting behaviour. Two source-specific extractors feed one
# verdict; used by both digests.
# ---------------------------------------------------------------------------
def flat_motivated(entry):
    """price_history entry -> motivated-seller info dict, or None.

    Signals: CenuMednieks original_price -> current_price drop,
    days_on_market, previous_listings count, plus drops inside our own
    observation trail. Needs at least one source of evidence."""
    if not isinstance(entry, dict):
        return None
    c = entry.get("cenumednieks") or {}
    op, cp = c.get("original_price"), c.get("current_price")
    cenu_drop = cenu_pct = 0.0
    try:
        if op and cp and float(cp) < float(op):
            cenu_drop = float(op) - float(cp)
            cenu_pct = cenu_drop / float(op) * 100
    except (TypeError, ValueError):
        pass
    # our_tracking rows are {"date", "price"} dicts (price_history writes
    # them) — read both keys so legacy/"p"-shaped rows still decode.
    obs = []
    for o in (entry.get("our_tracking") or []):
        v = o.get("price") if isinstance(o, dict) else None
        if v is None and isinstance(o, dict):
            v = o.get("p")
        v = to_float(v)
        if v is not None:
            obs.append(v)
    own_drops = sum(1 for a, b in zip(obs, obs[1:]) if b < a)
    own_drop = own_pct = 0.0
    if len(obs) >= 2 and obs[-1] < obs[0]:
        own_drop = float(obs[0]) - float(obs[-1])
        own_pct = own_drop / float(obs[0]) * 100
    # Take the larger signal: cenu normally wins (longer history), but a
    # cenu dict without usable prices must not shadow a real own-trail
    # drop (a bare {days_on_market} cenu used to zero out the drop).
    if cenu_drop >= own_drop:
        drop_eur, drop_pct, was, now = cenu_drop, cenu_pct, op, cp
    else:
        drop_eur, drop_pct, was, now = own_drop, own_pct, obs[0], obs[-1]
    if not drop_eur and not own_drops and not c:
        return None
    # at_low: current ask is the lowest point in the whole observed
    # record (cenu original/current + our trail) AND something was once
    # higher — the best moment to make an offer.
    cur = to_float(cp if c else (obs[-1] if obs else None))
    all_prices = ([to_float(op)] if c and to_float(op) is not None else [])
    all_prices += [to_float(p) for p in obs if to_float(p) is not None]
    if cur is not None:
        all_prices.append(cur)
    at_low = bool(cur is not None and all_prices
                  and cur <= min(all_prices)
                  and len(set(all_prices)) > 1)
    return {
        "drop_eur": drop_eur, "drop_pct": drop_pct,
        "days": c.get("days_on_market") or 0,
        "relists": len(c.get("previous_listings") or []),
        "trail_drops": own_drops,
        "at_low": at_low,
        "was": was if drop_eur else None,
        "now": now if drop_eur else None,
    }


def car_motivated(l, today=None):
    """car listing dict -> motivated-seller info dict, or None.

    Uses the _price_hist trail and _first_seen annotation cars.run()
    attaches from car_seen.json. days = days since first seen.
    A _relisted annotation contributes the vanished ad's last ask as the
    trail's first point — reposting under a new id wipes _price_hist, so
    without it a repost-at-a-cut would look like a brand-new ad."""
    hist = []
    r = l.get("_relisted") or {}
    try:
        if r.get("p") is not None and r.get("gone"):
            hist.append((str(r["gone"]), float(r["p"])))
    except (TypeError, ValueError):
        pass
    for point in l.get("_price_hist") or []:
        try:
            hist.append((point[0], float(point[1])))
        except (TypeError, ValueError, IndexError):
            continue
    drop_eur = drop_pct = 0.0
    n_drops = 0
    if len(hist) >= 2:
        n_drops = sum(1 for a, b in zip(hist, hist[1:]) if b[1] < a[1])
        if hist[-1][1] < hist[0][1]:
            drop_eur = hist[0][1] - hist[-1][1]
            drop_pct = drop_eur / hist[0][1] * 100
    if isinstance(today, str):
        try:
            today = date.fromisoformat(today)
        except ValueError:
            today = None
    days = days_since(l.get("_first_seen"), today=today) or 0
    if not drop_eur and not n_drops:
        return None
    prices = [p for _, p in hist]
    at_low = bool(prices and hist[-1][1] <= min(prices)
                  and len(set(prices)) > 1)
    return {
        "drop_eur": drop_eur, "drop_pct": drop_pct, "days": days,
        "relists": 1 if r.get("p") is not None else 0,
        "trail_drops": n_drops, "at_low": at_low,
        "was": hist[0][1] if drop_eur else None,
        "now": hist[-1][1] if drop_eur else None,
    }


def is_motivated(info, stale_days, min_drop_eur):
    """Verdict: a real drop AND (stale listing or repeated cutting)."""
    if not info or info.get("drop_eur", 0) < min_drop_eur:
        return False
    repeated = (info.get("trail_drops", 0) >= config.MOTIVATED_MIN_TRAIL_DROPS
                or info.get("relists", 0) >= config.MOTIVATED_MIN_RELISTINGS)
    return info.get("days", 0) >= stale_days or repeated


def flat_is_motivated(info):
    """is_motivated() with the flat thresholds from config — single home
    for the (stale_days, min_drop_eur) pair callers used to repeat."""
    return is_motivated(info, config.MOTIVATED_STALE_DAYS_FLAT,
                        config.MOTIVATED_MIN_DROP_EUR_FLAT)


def car_is_motivated(info):
    """is_motivated() with the car thresholds from config."""
    return is_motivated(info, config.MOTIVATED_STALE_DAYS_CAR,
                        config.MOTIVATED_MIN_DROP_EUR_CAR)


def older_than_days(date_str, days, today=None):
    """True when date_str is missing/unparseable or older than `days` —
    the retry-gate convention used for weekly-refetch style stamps."""
    n = days_since(date_str, today=today)
    return True if n is None else n >= days


def retry_after_seconds(resp):
    """Parse the Retry-After response header (seconds form), capped at 30 s.
    Shared by the flat and car ss.com fetchers' 429/5xx retry."""
    try:
        return min(float(resp.headers.get("Retry-After", "")), 30.0)
    except (TypeError, ValueError):
        return None


def upsert_history_point(points, run_date, point, max_points):
    """Insert `point` ([date, ...]) into `points` sorted by date; a same-date
    point is replaced wherever it sits, then the series is capped at
    max_points. Shared by the flat/car market history writers — a backfill
    merged after live appends had produced unordered duplicate tails."""
    for i, p in enumerate(points):
        if p[0] == run_date:
            points[i] = point
            break
        if str(p[0]) > run_date:
            points.insert(i, point)
            break
    else:
        points.append(point)
    del points[:-max_points]


def delta_7d(points, value_idx=1):
    """% change of points[-1][value_idx] vs the newest point >= 7 days old.

    points = [[date, value, ...], ...] ascending. None when the series
    has no point old enough to compare against."""
    if len(points) < 2:
        return None
    try:
        latest_date = date.fromisoformat(str(points[-1][0]))
    except (ValueError, TypeError):
        return None
    cutoff = (latest_date - timedelta(days=7)).isoformat()
    base = None
    for p in points:
        if str(p[0]) <= cutoff:
            base = p
    if base is None or not base[value_idx] or not points[-1][value_idx]:
        return None
    return 100.0 * (points[-1][value_idx] - base[value_idx]) / base[value_idx]


def sparkline_svg(points, w=64, h=16, title=None):
    """Tiny inline-SVG price/value trail (red = dropping, green = rising,
    grey = flat). ``points`` is any [(x, y), ...] sequence — only the y
    values are drawn. '' when fewer than two usable values exist.
    All values are numbers we generated, so no escaping is needed."""
    pts = []
    for point in points or []:
        try:
            pts.append(float(point[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(pts)
    coords = " ".join(
        f"{round(i * (w - 4) / (n - 1) + 2, 1)},"
        f"{round(h - 3 - (v - lo) / span * (h - 6), 1)}"
        for i, v in enumerate(pts))
    # var() works in SVG presentation attrs and follows the page theme.
    color = ("var(--bad)" if pts[-1] < pts[0]
             else "var(--good)" if pts[-1] > pts[0] else "var(--muted)")
    title_attr = f" title='{esc(title)}'" if title else ""
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'style="vertical-align:-3px;margin-left:4px"{title_attr}>'
            f'<polyline points="{coords}" fill="none" stroke="{color}" '
            f'stroke-width="1.5"/></svg>')


def strip_diacritics(text):
    """'Šampēteris' -> 'Sampeteris', 'Zolitūde' -> 'Zolitude'."""
    if text is None:
        return ""
    nf = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nf if not unicodedata.combining(c))


def match_district(raw_name):
    """Return canonical district name if raw_name matches a target, else None.
    Matches case-insensitively after stripping diacritics."""
    if not raw_name:
        return None
    low = strip_diacritics(raw_name).lower()
    for canon, aliases in config.DISTRICTS.items():
        for alias in aliases:
            if strip_diacritics(alias).lower() in low:
                return canon
    return None


def safe_url(url):
    """Return url only if it is an absolute https:// link, else ''.

    Scraped hrefs end up in the public digest; anything that is not plain
    https (javascript:, data:, relative junk) is dropped rather than rendered.
    """
    u = str(url or "").strip()
    return u if re.match(r"^https://[^\s'\"<>]+$", u) else ""


def slugify(text):
    """'Krišjāņa Valdemāra iela' -> 'krisjana-valdemara-iela'."""
    s = strip_diacritics(text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# ---------------------------------------------------------------------------
# New-build exclusion
# ---------------------------------------------------------------------------
def is_new_build(listing):
    """True if the listing is in a newly built development.

    SS.com marks new builds with series = "New" (exact value).
    City24's series is a free-text project name, so we check for common
    new-build keywords there as well.
    """
    if not config.EXCLUDE_NEW_BUILDS:
        return False
    series = strip_diacritics(listing.get("series") or "").strip().lower()
    if series in [s.lower() for s in config.NEW_BUILD_SERIES]:
        return True
    text = " ".join([
        strip_diacritics(listing.get("series") or ""),
        strip_diacritics(listing.get("title") or ""),
    ]).lower()
    return any(kw in text for kw in config.NEW_BUILD_KEYWORDS)


def filter_new_builds(listings):
    """Remove new-build listings. Returns (filtered, n_removed)."""
    if not config.EXCLUDE_NEW_BUILDS:
        return listings, 0
    kept = [l for l in listings if not is_new_build(l)]
    removed = len(listings) - len(kept)
    return kept, removed


# ---------------------------------------------------------------------------
# Cross-source deduplication
# ---------------------------------------------------------------------------
def _street_tokens(street):
    """Normalise a street string to comparable tokens, dropping house numbers.
    'Dammes iela 12' -> {'dammes', 'iela'}"""
    s = strip_diacritics(street or "").lower()
    s = re.sub(r"[^a-z\s]+", " ", s)  # strip digits/punctuation
    return {t for t in s.split() if len(t) > 2}


def _same_flat(a, b):
    """Heuristic: are these two listings the same physical flat?

    Requires identical deal_type/district/rooms, area within
    DEDUPE_AREA_TOL_M2 and price within DEDUPE_PRICE_TOL_PCT. If both listings
    carry a street name, they must share at least one street token - this
    prevents merging two genuinely different flats that happen to have the same
    size and price.
    """
    try:
        area_a, area_b = float(a.get("area_m2") or 0), float(b.get("area_m2") or 0)
        price_a, price_b = float(a.get("price_eur") or 0), float(b.get("price_eur") or 0)
    except (TypeError, ValueError):
        return False
    if not area_a or not area_b or not price_a or not price_b:
        return False

    if abs(area_a - area_b) > config.DEDUPE_AREA_TOL_M2:
        return False
    tol = max(price_a, price_b) * (config.DEDUPE_PRICE_TOL_PCT / 100.0)
    if abs(price_a - price_b) > tol:
        return False

    ta, tb = _street_tokens(a.get("street")), _street_tokens(b.get("street"))
    if ta and tb and not (ta & tb):
        return False
    return True


def _source_rank(listing):
    try:
        return config.DEDUPE_SOURCE_PRIORITY.index(listing.get("source"))
    except (ValueError, AttributeError):
        return len(config.DEDUPE_SOURCE_PRIORITY)


def dedupe_cross_source(listings):
    """Merge listings that represent the same flat on different portals.

    The same flat is frequently posted on both ss.com and city24.lv under
    different IDs. Left unmerged it appears twice in the digest AND is counted
    twice in the training data, distorting the price baseline.

    Keeps one listing per cluster (preferring DEDUPE_SOURCE_PRIORITY order, then
    the one with a street address) and records the other portals on the survivor
    as 'also_on'. Returns (deduped_list, n_merged).
    """
    if not config.DEDUPE_ENABLED or not listings:
        return listings, 0

    # bucket by exact attributes first so we only compare plausible pairs
    buckets = {}
    for l in listings:
        key = (l.get("deal_type"), l.get("district"), l.get("rooms"))
        buckets.setdefault(key, []).append(l)

    result = []
    merged = 0
    for _key, group in buckets.items():
        clusters = []
        for l in group:
            for cluster in clusters:
                if _same_flat(cluster[0], l):
                    cluster.append(l)
                    break
            else:
                clusters.append([l])

        for cluster in clusters:
            if len(cluster) == 1:
                result.append(cluster[0])
                continue
            # pick survivor: source priority, then presence of a street address
            cluster.sort(key=lambda x: (_source_rank(x), 0 if x.get("street") else 1))
            survivor = cluster[0]
            others = cluster[1:]
            survivor["also_on"] = sorted({o.get("source") for o in others
                                          if o.get("source") != survivor.get("source")})
            # the same flat is sometimes cheaper on the OTHER portal —
            # record the lowest alternate price so the digest can say
            # "(€2 000 less on city24.lv)". That's real deal intel.
            # Clear any stale flag first (dedupe mutates listings; a
            # re-deduped survivor could carry an outdated value).
            survivor.pop("also_cheaper", None)
            surv_price = survivor.get("price_eur")
            cheaper = [o for o in others
                       if o.get("source") != survivor.get("source")
                       and o.get("price_eur") and surv_price
                       and o["price_eur"] < surv_price]
            if cheaper:
                c = min(cheaper, key=lambda o: o["price_eur"])
                survivor["also_cheaper"] = {
                    "price": c["price_eur"],
                    "source": c.get("source"),
                    "url": c.get("url") or "",
                }
            merged += len(others)
            result.append(survivor)

    if merged:
        print(f"[dedupe] merged {merged} cross-source duplicate listing(s)")
    return result, merged
