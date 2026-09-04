# -*- coding: utf-8 -*-
"""
city24.lv scraper.

city24.lv is a JavaScript SPA that fetches listings from a JSON API at
api.city24.lv/<locale>/search/realties protected by an anti-bot X-Anon-Token
header. Rather than reverse-engineer the token, we render the search pages in
a headless Chromium (Playwright) and intercept the JSON responses the browser
already makes. This is robust against token rotation.

API response = a bare JSON list of items. Each item schema (verified):
  id, friendly_id, price, price_per_unit, room_count, property_size,
  address.{district_name, street_name, house_number, city_name},
  attributes.{FLOOR, TOTAL_FLOORS}, project.project_name, slogans, date_published

We walk pages /pg=1 .. /pg=N, collect items, and keep only those whose
address.district_name matches one of our target districts.
"""
import json

import config
from utils import match_district, slugify

API_HOST = "api.city24.lv"
API_PATH_MARKER = "search/realties"


def _extract_item(item, deal_type):
    """Convert one API item dict -> unified listing dict, or None if not a target district."""
    addr = item.get("address") or {}
    district_raw = addr.get("district_name") or ""
    district = match_district(district_raw)
    if district is None:
        return None  # outside our target districts

    street = addr.get("street_name") or ""
    house = addr.get("house_number") or ""
    street_full = " ".join(p for p in [street, house] if p).strip()

    attrs = item.get("attributes") or {}
    floor_num = attrs.get("FLOOR")
    floor_total = attrs.get("TOTAL_FLOORS")
    if floor_num is not None and floor_total is not None:
        floor_text = f"{floor_num}/{floor_total}"
    elif floor_num is not None:
        floor_text = str(floor_num)
    else:
        floor_text = ""

    project = item.get("project") or {}
    series = project.get("project_name") or ""

    rooms = item.get("room_count")
    area = item.get("property_size")
    price = item.get("price")
    ppu = item.get("price_per_unit")

    if price is None or area is None or area <= 0:
        return None

    # listing detail URL: /real-estate/apartments-for-{deal}/riga-{district-slug}-{street-slug}/{friendly_id}
    friendly_id = item.get("friendly_id")
    slug_parts = [p for p in ["riga", slugify(district_raw), slugify(street)] if p]
    slug = "-".join(slug_parts)
    url = ""
    if friendly_id:
        url = f"https://www.city24.lv/real-estate/apartments-for-{deal_type}/{slug}/{friendly_id}"

    slogans = item.get("slogans") or []
    title = " ".join(slogans).strip() if isinstance(slogans, list) else ""
    if not title:
        title = f"{rooms} room(s), {area} m², {district_raw} - {street_full}".strip(" -")

    return {
        "source": "city24.lv",
        "deal_type": deal_type,
        "id": str(item.get("id") or friendly_id or ""),
        "url": url,
        "district": district,
        "street": street_full,
        "rooms": int(rooms) if rooms is not None else None,
        "area_m2": float(area),
        "floor": floor_text,
        "floor_num": int(floor_num) if floor_num is not None else None,
        "floor_total": int(floor_total) if floor_total is not None else None,
        "series": series,
        "price_eur": float(price),
        "price_per_m2": float(ppu) if ppu is not None else round(float(price) / float(area), 2),
        "title": title,
    }


def _scrape_deal_type(playwright, deal_type):
    """Walk search pages for one deal type, return list of unified listings."""
    from playwright.sync_api import sync_playwright  # noqa (kept for clarity)

    base = config.CITY24_SEARCH_URL[deal_type]
    results = []
    page_size = None

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(user_agent=config.CITY24_USER_AGENT)
    page = context.new_page()

    captured = []

    def on_response(resp):
        try:
            if API_HOST in resp.url and API_PATH_MARKER in resp.url:
                captured.append(resp.json())
        except Exception:
            pass

    page.on("response", on_response)

    try:
        for pg in range(1, config.CITY24_MAX_PAGES + 1):
            captured.clear()
            url = f"{base}/pg={pg}"
            try:
                page.goto(url, timeout=config.CITY24_NAV_TIMEOUT, wait_until="networkidle")
            except Exception as e:
                print(f"[city24.lv] goto page {pg} failed: {e}")
                break
            page.wait_for_timeout(1500)

            page_items = []
            for payload in captured:
                if isinstance(payload, list):
                    page_items.extend(payload)
                elif isinstance(payload, dict):
                    # some wrappers: items / realities / data
                    for key in ("items", "realties", "data", "results"):
                        if isinstance(payload.get(key), list):
                            page_items.extend(payload[key])
                            break

            if not page_items:
                break  # nothing more

            for it in page_items:
                item = _extract_item(it, deal_type)
                if item:
                    results.append(item)

            page_size = page_size or len(page_items)
            if len(page_items) < (page_size or 50):
                break  # last page reached
    finally:
        context.close()
        browser.close()

    # de-duplicate by id within this run
    seen = set()
    deduped = []
    for r in results:
        if r["id"] and r["id"] not in seen:
            seen.add(r["id"])
            deduped.append(r)
    print(f"[city24.lv] {deal_type}: {len(deduped)} listings in target districts")
    return deduped


def scrape(deal_type):
    """Return list of listing dicts for the given deal_type, or [] on failure."""
    if not config.CITY24_ENABLED:
        return []
    if deal_type not in config.CITY24_SEARCH_URL:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[city24.lv] playwright not installed -> skipping")
        return []
    try:
        with sync_playwright() as p:
            return _scrape_deal_type(p, deal_type)
    except Exception as e:
        print(f"[city24.lv] {deal_type} failed: {e}")
        return []


if __name__ == "__main__":
    for dt in config.DEAL_TYPES:
        items = scrape(dt)
        for it in items[:5]:
            print(it["deal_type"], it["district"], it["rooms"], "r",
                  it["area_m2"], "m2", it["floor"], it["price_eur"], "EUR",
                  it["price_per_m2"], "EUR/m2", it["url"])
