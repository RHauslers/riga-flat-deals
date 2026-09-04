# -*- coding: utf-8 -*-
"""Probe city24 API response for coordinate fields."""
import json
import requests

# Try a direct API call (may be blocked by anti-bot, but worth trying)
url = "https://api.city24.lv/lv_LV/search/realties"
params = {
    "deal_type": "1",  # 1 = rent, 2 = sale
    "county": "1",
    "city": "2",  # Riga
    "page": "1",
    "items_per_page": "5",
}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}
try:
    r = requests.get(url, params=params, headers=headers, timeout=15)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        if items and len(items) > 0:
            item = items[0]
            # Print all keys to find coordinate fields
            print("All keys:", sorted(item.keys()))
            # Check for common coordinate field names
            for key in ['lat', 'lon', 'latitude', 'longitude', 'geo_lat', 'geo_lon',
                        'map_lat', 'map_lon', 'coordinates', 'location', 'point',
                        'geo_point', 'position']:
                if key in item:
                    print(f"  {key}: {item[key]}")
            # Check nested address object
            addr = item.get('address', {})
            print("Address keys:", sorted(addr.keys()) if addr else "none")
            for key in ['lat', 'lon', 'latitude', 'longitude', 'geo_lat', 'geo_lon']:
                if key in addr:
                    print(f"  address.{key}: {addr[key]}")
        else:
            print("No items in response")
            print(str(data)[:500])
    else:
        print(f"Body: {r.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
