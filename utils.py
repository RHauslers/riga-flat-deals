# -*- coding: utf-8 -*-
"""Shared helpers: diacritic stripping, district matching, slugify."""
import unicodedata
import re

import config


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


def slugify(text):
    """'Krišjāņa Valdemāra iela' -> 'krisjana-valdemara-iela'."""
    s = strip_diacritics(text or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


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
            merged += len(others)
            result.append(survivor)

    if merged:
        print(f"[dedupe] merged {merged} cross-source duplicate listing(s)")
    return result, merged
