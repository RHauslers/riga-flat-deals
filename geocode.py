# -*- coding: utf-8 -*-
"""
Geocoding for listings — converts street addresses to lat/lon coordinates
so listings can be shown on a map.

Two approaches:
1. City24.lv: the API already returns latitude/longitude directly — no
   geocoding needed, we just capture the fields in the scraper.
2. SS.com: we geocode the street address using Nominatim (OpenStreetMap's
   free geocoder, no API key, 1 req/sec rate limit).

Results are cached in data/geocode_cache.json so we only geocode each
address once (streets don't move).
"""
import math
import os
import re
import time
from datetime import date, datetime

import requests

import config
import utils
from utils import safe_url


def _e(v):
    return utils.esc(v)


GEOCODE_CACHE_JSON = config.GEOCODE_CACHE_JSON

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "FlatSearcher/1.0 (riga-flat-deals)"
NOMINATIM_TIMEOUT = 10
NOMINATIM_DELAY = 1.1  # seconds between requests (rate limit: 1/sec)


# ---------------------------------------------------------------------------
# School proximity (Rīgas Ziemeļvalstu ģimnāzija)
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in kilometres."""
    r = 6371.0  # Earth radius km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def distance_to_school(listing):
    """Distance in km from a listing to the school, or None if no coords."""
    lat = listing.get("lat")
    lon = listing.get("lon")
    if lat is None or lon is None:
        return None
    return haversine_km(lat, lon, config.SCHOOL_LAT, config.SCHOOL_LON)


def proximity_score(listing):
    """Proximity z-equivalent score for a listing (sale ranking only).

    Linear from +2.0 (next door) to -1.5 (far edge of PROXIMITY_MAX_KM).
    Returns 0.0 when coordinates are missing (neutral, no penalty — the
    listing might still be close, we just can't tell).
    """
    d = distance_to_school(listing)
    if d is None:
        return 0.0
    # +2.0 at 0km, decreasing by ~1.0 per km, floor at -1.5
    return max(-1.5, 2.0 - d)


def _read_json(path, default):
    return utils.read_json(path, default)


def _write_json(path, data):
    utils.write_json(path, data, indent=None)  # compact — committed daily


def load_cache():
    return _read_json(GEOCODE_CACHE_JSON, {})


def save_cache(data):
    _write_json(GEOCODE_CACHE_JSON, data)


# ---------------------------------------------------------------------------
# Address normalisation
#
# SS.com's English pages transliterate and abbreviate Latvian street names in
# ways Nominatim does not understand ("anninmuizhas 20" = Anniņmuižas iela
# 20, "jurmalas g. 82/2" = Jūrmalas gatve 82, "m. krūmu 18" = Mazā Krūmu
# iela 18, "imantas 16. l. 18" = Imantas 16. līnija 18). Before the 2026-09-30
# fix ~13% of SS.com flats — several right next to the school — never got
# coordinates. The functions below turn the raw text into a short list of
# progressively looser query candidates.
# ---------------------------------------------------------------------------
_STREET_TYPE_WORDS = ("iela", "gatve", "prospekts", "linija", "līnija",
                      "bulvaris", "bulvāris", "dambis", "laukums", "soseja",
                      "šoseja", "aleja", "cels", "ceļš", "krastmala")
_ABBREVIATIONS = {"g.": "gatve", "pr.": "prospekts", "l.": "līnija",
                  "b.": "bulvāris", "d.": "dambis", "lauk.": "laukums",
                  "šos.": "šoseja", "sos.": "šoseja"}
_LEADING_ABBREVIATIONS = {"m.": "Mazā", "l.": "Lielā"}
# SS.com truncates long cells ("anninmuizhas boule..", "... stree.."): the
# cut-off word is matched as a PREFIX of these English/Latvian street types.
# Getting the type right matters — Anniņmuižas iela and Anniņmuižas
# bulvāris are different streets ~1 km apart.
_TRUNCATED_TYPES = {"street": "iela", "boulevard": "bulvāris",
                    "avenue": "gatve", "prospect": "prospekts",
                    "line": "līnija", "iela": "iela", "bulvaris": "bulvāris",
                    "gatve": "gatve", "prospekts": "prospekts",
                    "linija": "līnija", "dambis": "dambis"}
_TRANSLIT = (("sh", "s"), ("zh", "z"), ("ch", "c"))
_NUMBER_RE = re.compile(r"^(\d+)([a-z]?)(?:\s*(?:/|k|k-|korp\.?)\s*(\d+))?$", re.I)


def _split_number(text):
    """'Jūrmalas gatve 82/2' -> ('Jūrmalas gatve', '82/2'); no number -> (text, '')."""
    m = re.match(r"^(.*?)[\s,]+(\d+[^\s,]*)\s*$", text.strip())
    if m and not re.search(r"\d\.\s*$", m.group(1)):  # keep '16.' in '16. l.'
        return m.group(1).strip(" ,"), m.group(2)
    return text.strip(), ""


def _normalize_street(raw):
    """Best-effort Latvian street name from SS.com's English rendering.
    Returns the normalised street (without house number) or ''."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"\.\.+$", "", s).strip()          # truncated '...' tail
    if not s:
        return ""
    words = s.split()
    # a truncated final word ("stree", "boule") is resolved to its street
    # type by prefix, or dropped (the default "iela" is re-added below)
    if raw.rstrip().endswith("..") and words:
        frag = words[-1].lower()
        hit = next((v for k, v in _TRUNCATED_TYPES.items()
                    if len(frag) >= 3 and k.startswith(frag)), None)
        words = words[:-1] + ([hit] if hit else [])
    # a dangling single letter ("imantas 3. l. c") is an orphaned house
    # letter, not part of the street name
    while words and len(words[-1]) == 1 and words[-1].isalpha():
        words = words[:-1]
    if not words:
        return ""
    lw = [w.lower() for w in words]
    if lw[0] in _LEADING_ABBREVIATIONS:
        words[0] = _LEADING_ABBREVIATIONS[lw[0]]
    out = []
    for w in words:
        key = w.lower()
        if key in _ABBREVIATIONS:
            out.append(_ABBREVIATIONS[key])
        else:
            for a, b in _TRANSLIT:
                key = key.replace(a, b)
            out.append(key)
    has_type = any(w.lower().rstrip(".") in _STREET_TYPE_WORDS for w in out)
    if not has_type:
        out.append("iela")
    return " ".join(out)


def address_candidates(address):
    """Ordered list of (query, precision) pairs to try for one SS.com/auction
    street string. precision is 'house' or 'street'."""
    if not address:
        return []
    street_raw, number = _split_number(address)
    street = _normalize_street(street_raw)
    if not street:
        return []
    cands = []
    seen = set()

    def _add(q, precision):
        q = re.sub(r"\s+", " ", q).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            cands.append((q, precision))

    if number:
        m = _NUMBER_RE.match(number)
        if m:
            base, letter, korp = m.group(1), m.group(2), m.group(3)
            if korp:
                _add(f"{street} {base} k-{korp}", "house")
            if letter:
                _add(f"{street} {base}{letter}", "house")
            _add(f"{street} {base}", "house")
        else:
            _add(f"{street} {number}", "house")
    _add(street, "street")
    return cands


def _nominatim(query):
    """One Nominatim request. Returns (lat, lon) or (None, None)."""
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": f"{query}, Riga, Latvia", "format": "json",
                    "limit": 1, "countrycodes": "lv", "addressdetails": 0},
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=NOMINATIM_TIMEOUT,
        )
        if r.status_code != 200:
            return None, None
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        pass
    return None, None


def _plausible(lat, lon):
    """Reject hits far from the school — Nominatim occasionally returns a
    same-named street in another Latvian town."""
    if lat is None or lon is None:
        return False
    return haversine_km(lat, lon, config.SCHOOL_LAT, config.SCHOOL_LON) \
        <= config.GEOCODE_MAX_KM_FROM_SCHOOL


def _geocode_address(address, district="Riga"):
    """Geocode a street string via Nominatim, trying normalised candidates
    from most to least specific.

    Returns (lat, lon, precision, n_requests); precision is 'house',
    'street' or None when nothing matched. Each request is followed by the
    rate-limit pause. ``district`` is accepted for backward compatibility
    but no longer used in the query — canonical ASCII district names
    ("Sampeteris") hurt Nominatim more than they help, and the distance
    guard in _plausible() does the disambiguation instead.
    """
    n = 0
    for query, precision in address_candidates(address):
        lat, lon = _nominatim(query)
        n += 1
        time.sleep(NOMINATIM_DELAY)  # respect rate limit
        if _plausible(lat, lon):
            return lat, lon, precision, n
    return None, None, None, n


def _cache_key(listing):
    """Build a cache key from source + street address."""
    street = listing.get("street", "").strip().lower()
    district = listing.get("district", "").strip().lower()
    return f"{listing.get('source')}:{district}:{street}"


def enrich_coordinates(listings):
    """Add lat/lon to each listing that doesn't already have coordinates.

    For city24 listings: coordinates are already set by the scraper.
    For SS.com listings: geocode the street address via Nominatim (cached).

    Returns the same list with lat/lon fields populated where possible.
    Also saves the geocode cache.
    """
    cache = load_cache()
    today = date.today().isoformat()
    n_geocoded = 0
    n_cached = 0
    n_skipped = 0
    n_failed = 0
    n_requests = 0

    for listing in listings:
        # Already has coordinates (city24 API provides them)
        if listing.get("lat") and listing.get("lon"):
            listing.setdefault("geo_precision", "house")
            n_skipped += 1
            continue

        # No street address — can't geocode
        if not listing.get("street"):
            continue

        key = _cache_key(listing)
        cached = cache.get(key)

        if cached and cached.get("lat") is not None:
            listing["lat"] = cached["lat"]
            listing["lon"] = cached["lon"]
            listing["geo_precision"] = cached.get("precision") or "house"
            n_cached += 1
            continue
        # A cached MISS is retried only after GEOCODE_RETRY_FAILED_DAYS.
        if cached and not _is_older_than_days(cached.get("fetched_at"),
                                              config.GEOCODE_RETRY_FAILED_DAYS):
            n_failed += 1
            continue

        lat, lon, precision, n_req = _geocode_address(listing.get("street", ""))
        n_requests += n_req
        cache[key] = {"lat": lat, "lon": lon, "precision": precision,
                      "fetched_at": today}
        n_geocoded += 1

        if lat is not None:
            listing["lat"] = lat
            listing["lon"] = lon
            listing["geo_precision"] = precision
        else:
            n_failed += 1

    if n_geocoded or n_cached:
        save_cache(cache)
        print(f"[geocode] {n_geocoded} new lookups ({n_requests} requests), "
              f"{n_cached} cached hits, {n_skipped} already had coords, "
              f"{n_failed} without coordinates")

    return listings


def _is_older_than_days(date_str, days):
    if not date_str:
        return True
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return True
    return (date.today() - d).days >= days


def coverage(listings):
    """(n_with_coords, n_total) over listings that have a street or coords —
    the health check's input."""
    total = sum(1 for l in listings if l.get("street") or l.get("lat"))
    with_coords = sum(1 for l in listings
                      if l.get("lat") is not None and l.get("lon") is not None)
    return with_coords, total


def get_map_data(listings):
    """Build a list of map markers from listings that have coordinates.

    Each marker: {lat, lon, popup_html, deal_type, price, score, source, url}
    """
    markers = []
    for listing in listings:
        lat = listing.get("lat")
        lon = listing.get("lon")
        if lat is None or lon is None:
            continue

        price = listing.get("price_eur", 0) or 0
        deal_type = _e(listing.get("deal_type", ""))
        score = listing.get("_score")  # may not be set yet
        source = _e(listing.get("source", ""))
        url = safe_url(listing.get("url", ""))
        district = _e(listing.get("district", ""))
        rooms = listing.get("rooms", "?")
        area = listing.get("area_m2", "?")
        floor = listing.get("floor", "?")

        # Build popup HTML (all scraped text escaped — it is injected into
        # the page via Leaflet's bindPopup, which renders HTML)
        rooms_disp = _e(rooms if rooms is not None else "?")
        area_disp = _e(area if area is not None else "?")
        floor_disp = _e(floor if floor not in (None, "") else "?")
        approx = "~" if listing.get("geo_precision") == "street" else ""
        popup = (
            f"<div style='font-family:Arial,sans-serif;font-size:13px;min-width:200px'>"
            f"<b>{district}</b> &middot; {deal_type}<br>"
            f"{rooms_disp} rooms &middot; {area_disp} m² &middot; floor {floor_disp}<br>"
            f"<b style='font-size:15px'>{price:,.0f} EUR</b>"
        )
        if listing.get("price_per_m2"):
            popup += f" <span style='color:#666'>({listing['price_per_m2']:.0f} EUR/m²)</span>"
        if score is not None:
            popup += f"<br>Deal score: <b>{score:+.2f}</b>"
        if listing.get("street"):
            popup += f"<br><span style='color:#666'>{approx}{_e(listing['street'])}</span>"
            if approx:
                popup += (" <span style='color:#999;font-size:11px'>"
                          "(street-level position)</span>")
        if listing.get("series") == "Auction":
            popup += "<br><b style='color:#8e44ad'>State/bailiff auction</b>"
            if listing.get("ownership_share"):
                popup += (f"<br><b style='color:#c0392b'>SHARE: "
                          f"{_e(listing['ownership_share'])} of the flat</b>")
            if listing.get("auction_start_price"):
                popup += (f"<br>Start: "
                          f"{listing['auction_start_price']:,.0f} EUR")
            if listing.get("auction_current_bid"):
                popup += (f"<br>Current bid: "
                          f"{listing['auction_current_bid']:,.0f} EUR")
            if listing.get("auction_end"):
                popup += f"<br>Ends: {_e(listing['auction_end'])}"
        if url:
            popup += (f"<br><a href='{_e(url)}' target='_blank' "
                      f"rel='noopener noreferrer'>View on {source} &rarr;</a>")
        popup += "</div>"

        markers.append({
            "marker_id": f"{source}:{listing.get('id')}",
            "lat": lat,
            "lon": lon,
            "popup": popup,
            "deal_type": deal_type,
            "price": price,
            "source": source,
            "series": listing.get("series", ""),
        })

    return markers


def get_school_marker():
    """A special marker for the school, rendered distinctly on the map."""
    popup = (
        f"<div style='font-family:Arial,sans-serif;font-size:13px;min-width:200px'>"
        f"<b>{config.SCHOOL_NAME}</b><br>"
        f"{config.SCHOOL_ADDRESS}<br>"
        f"<span style='color:#666'>Reference point for proximity ranking</span>"
        f"</div>"
    )
    return {
        "marker_id": "school:zvg",
        "lat": config.SCHOOL_LAT,
        "lon": config.SCHOOL_LON,
        "popup": popup,
        "deal_type": "school",
        "price": 0,
        "source": "school",
    }
