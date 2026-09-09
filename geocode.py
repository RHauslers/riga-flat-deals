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
import json
import math
import os
import time
from datetime import date

import requests

import config

GEOCODE_CACHE_JSON = os.path.join(config.DATA_DIR, "geocode_cache.json")

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
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    return _read_json(GEOCODE_CACHE_JSON, {})


def save_cache(data):
    _write_json(GEOCODE_CACHE_JSON, data)


def _geocode_address(address, district="Riga"):
    """Geocode an address string using Nominatim.

    Returns (lat, lon) tuple or (None, None) if not found.
    """
    if not address:
        return None, None

    # Build a search query that includes Riga, Latvia for accuracy
    query = f"{address}, {district}, Riga, Latvia"

    try:
        r = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "lv",
                "addressdetails": 0,
            },
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=NOMINATIM_TIMEOUT,
        )
        if r.status_code != 200:
            return None, None
        results = r.json()
        if results and len(results) > 0:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            return lat, lon
    except (requests.RequestException, ValueError, KeyError, IndexError):
        pass

    return None, None


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

    for listing in listings:
        # Already has coordinates (city24 API provides them)
        if listing.get("lat") and listing.get("lon"):
            n_skipped += 1
            continue

        # No street address — can't geocode
        if not listing.get("street"):
            continue

        key = _cache_key(listing)
        cached = cache.get(key)

        # Use cached result if it exists (and isn't a failed lookup we should retry)
        if cached and (cached.get("lat") is not None or cached.get("tried_today") == today):
            if cached.get("lat") is not None:
                listing["lat"] = cached["lat"]
                listing["lon"] = cached["lon"]
                n_cached += 1
            continue

        # Geocode via Nominatim
        lat, lon = _geocode_address(
            listing.get("street", ""),
            listing.get("district", "Riga"),
        )
        cache[key] = {"lat": lat, "lon": lon, "fetched_at": today}
        n_geocoded += 1

        if lat is not None:
            listing["lat"] = lat
            listing["lon"] = lon

        time.sleep(NOMINATIM_DELAY)  # respect rate limit

    if n_geocoded or n_cached:
        save_cache(cache)
        print(f"[geocode] {n_geocoded} new lookups, {n_cached} cached hits, "
              f"{n_skipped} already had coords")

    return listings


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

        price = listing.get("price_eur", 0)
        deal_type = listing.get("deal_type", "")
        score = listing.get("_score")  # may not be set yet
        source = listing.get("source", "")
        url = listing.get("url", "")
        district = listing.get("district", "")
        rooms = listing.get("rooms", "?")
        area = listing.get("area_m2", "?")
        floor = listing.get("floor", "?")

        # Build popup HTML
        rooms_disp = rooms if rooms is not None else "?"
        area_disp = area if area is not None else "?"
        floor_disp = floor if floor not in (None, "") else "?"
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
        if listing.get("series") == "Auction":
            popup += "<br><b style='color:#8e44ad'>State/bailiff auction</b>"
            if listing.get("auction_start_price"):
                popup += (f"<br>Start: "
                          f"{listing['auction_start_price']:,.0f} EUR")
            if listing.get("auction_current_bid"):
                popup += (f"<br>Current bid: "
                          f"{listing['auction_current_bid']:,.0f} EUR")
            if listing.get("auction_end"):
                popup += f"<br>Ends: {listing['auction_end']}"
        if url:
            popup += f"<br><a href='{url}' target='_blank'>View on {source} &rarr;</a>"
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
