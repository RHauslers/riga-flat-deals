# -*- coding: utf-8 -*-
"""
izsoles.ta.gov.lv scraper — Latvian state/bailiff property auctions.

The e-auction site (administered by the State Land Service) lists
forced-sale auctions run by bailiffs (zvērināti tiesu izpildītāji) plus
state and municipal property. Starting prices are often well below
market because the goal is debt recovery, not profit.

How it works:
  1. POST the site's search form with:
       ownership_type = owner    (property rights, not lease)
       region         = 7        (Rīga)
       type           = 1        (real estate)
       category       = 3        (apartments / dzīvokļi)
       init-search    = on       (the submit button — without this the
                                  server silently ignores all filters)
  2. The result list exposes detail links /izsole/{uuid} whose anchor
     text is the property address ("Zalves iela 44A - 6, Rīga").
  3. Pagination (when results exceed one page) is path-based: /2, /3...
     fetched on the same session so the filter state carries over.
  4. Each detail page has a clean info-parameter / info-value div pair
     structure we parse for: starting price, current bid, deposit and
     the auction end date. Rooms and area only exist inside the legal
     description text, so they are extracted with regexes (area is
     filtered to apartment-sized values to skip land parcels).

Auctions are kept OUT of the main deal ranking on purpose: the
regression model trains on regular sale listings, and auction dynamics
(bids, deadlines, deposits) are not comparable. They feed a separate
digest section sorted by distance to the school.
"""
import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

import config

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# The site's search form fields. init-search=on is the submit button —
# the server ignores all filters without it (verified by probing).
SEARCH_PAYLOAD = {
    "ownership_type": "owner",
    "region": "7",          # Rīga
    "type": "1",            # real estate
    "category": "3",        # apartments (dzīvokļi)
    "start_time_from": "", "start_time_to": "",
    "end_time_from": "", "end_time_to": "",
    "search_string": "",
    "publisher_id": "",
    "auction_state": "",
    "stage": "",
    "area_from": "", "area_to": "",
    "start_price_from": "", "start_price_to": "",
    "valuation_from": "", "valuation_to": "",
    "auction_type": "",
    "auction_days": "",
    "is_cultural_value": "",
    "usage_goal": "",
    "announcement_filter_state_mask": "",
    "init-search": "on",
}


def _parse_eur(text):
    """'€ 31 900.00' -> 31900.0 ; 'nav' / '' / 0 -> None."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None
    # Latvian format: comma is the decimal separator
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        v = float(cleaned)
        return v if v > 0 else None
    except ValueError:
        return None


def _parse_lv_date(text):
    """'10.09.2026 13:00' -> '2026-09-10'."""
    if not text:
        return None
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).date().isoformat()
    except ValueError:
        return None


def _parse_list_page(html):
    """Extract (link_items, max_page) from one search-result page.

    Each link item: {url, title} where the title is the property address.
    Only Rīga addresses are kept (belt & suspenders on top of the region
    filter, which also protects us if pagination ever loses filter state).
    """
    soup = BeautifulSoup(html, "lxml")
    items = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/izsole/" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or "Rīga" not in title:
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append({"url": href, "title": title})

    # Path-based pagination: links like https://izsoles.ta.gov.lv/2
    max_page = 1
    paging = (soup.find("div", class_="paging")
              or soup.find("ul", class_="pagination"))
    if paging:
        for a in paging.find_all("a", href=True):
            m = re.search(r"/(\d+)$", a.get("href", ""))
            if m:
                max_page = max(max_page, int(m.group(1)))
    return items, max_page


def _parse_detail(html, url, title):
    """Parse one auction detail page into a unified listing dict, or None."""
    soup = BeautifulSoup(html, "lxml")

    # Structured key/value pairs: <div class="info-parameter">Label</div>
    # followed by <div class="info-value object-data">Value</div>
    params = {}
    for p in soup.find_all("div", class_="info-parameter"):
        label = p.get_text(strip=True).rstrip(":").strip()
        v = p.find_next_sibling("div")
        if v is not None:
            params[label] = v.get_text(strip=True)

    text = soup.get_text(" ", strip=True)

    start_price = _parse_eur(params.get("Sākumcena", ""))
    current_bid = _parse_eur(params.get("Aktuālais solījums", ""))
    appraisal = _parse_eur(params.get("Novērtējums", ""))
    deposit = _parse_eur(params.get("Nodrošinājuma maksa", ""))
    end_date = _parse_lv_date(params.get("Izsoles noslēgums", ""))
    reg_m = re.search(r"Pieteikties var līdz\s*:?\s*(\d{2}\.\d{2}\.\d{4})", text)
    reg_until = _parse_lv_date(reg_m.group(1)) if reg_m else None

    # Rooms / area live only inside the legal description text.
    # Room counts appear either as digits ("2-istabu") or Latvian word
    # forms ("divistabu" = two-room); many announcements state neither.
    rooms = None
    m = re.search(r"(\d+)\s*[-\s]?istabu", text)
    if m:
        rooms = int(m.group(1))
    else:
        for word, n in (("vienistabu", 1), ("divistabu", 2), ("trīsistabu", 3),
                        ("četr", 4), ("piecistabu", 5)):
            if word in text.lower():
                rooms = n
                break

    # Area: two word orders occur in the legal text —
    #   "platību 50,1 m2"  and  "45,56 m2 platībā".
    # Take the first apartment-sized value (10-500 m²); land parcels
    # sharing the property are much larger and must be skipped.
    area = None
    for am in re.finditer(
            r"plat[iī]b[au]\s+([\d]+[.,]?\d*)\s*m2"
            r"|([\d]+[.,]?\d*)\s*m2\s*plat[iī]bā",
            text, re.I):
        raw = am.group(1) or am.group(2)
        if not raw:
            continue
        try:
            f = float(raw.replace(",", "."))
        except ValueError:
            continue
        if 10 <= f <= 500:
            area = f
            break

    # Title is "Street House - Apt, Rīga" — split off the city, and a
    # building-level street (no apartment number) for geocoding.
    street_full = re.sub(r",\s*Rīga.*$", "", title).strip()
    street_geo = re.sub(r"\s*-\s*\d+\s*$", "", street_full).strip()

    # The realistic price today is the current bid, or the starting
    # price when nobody has bid yet.
    price = current_bid or start_price
    if price is None:
        return None  # unparsable pricing — skip the auction

    m_id = re.search(r"/izsole/([0-9a-f-]+)", url)
    listing_id = m_id.group(1) if m_id else url

    return {
        "source": "izsoles.ta.gov.lv",
        "deal_type": "sale",
        "id": listing_id,
        "url": url,
        "district": "Riga",
        "street": street_geo,
        "rooms": rooms,
        "area_m2": area,
        "floor": "",
        "floor_num": None,
        "floor_total": None,
        "series": "Auction",
        "price_eur": price,
        "price_per_m2": round(price / area, 2) if area else None,
        "price_unit": "mon",
        "title": title,
        # auction-specific fields
        "auction_start_price": start_price,
        "auction_current_bid": current_bid,
        "auction_appraisal": appraisal,
        "auction_deposit": deposit,
        "auction_end": end_date,
        "auction_register_until": reg_until,
    }


def scrape():
    """Return active Riga apartment auctions as unified listing dicts."""
    if not config.IZSOLES_ENABLED:
        return []

    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    # Warm-up GET (session cookie), then the filtered POST search.
    s.get(config.IZSOLES_BASE, timeout=config.IZSOLES_TIMEOUT)
    r = s.post(config.IZSOLES_BASE, data=SEARCH_PAYLOAD,
               timeout=config.IZSOLES_TIMEOUT)
    r.encoding = "utf-8"
    link_items, max_page = _parse_list_page(r.text)

    # Path-based pagination (filters are session-scoped).
    pages = min(max_page, config.IZSOLES_MAX_PAGES)
    for pg in range(2, pages + 1):
        try:
            r2 = s.get(f"{config.IZSOLES_BASE}/{pg}",
                       timeout=config.IZSOLES_TIMEOUT)
            r2.encoding = "utf-8"
            pg_items, _ = _parse_list_page(r2.text)
        except requests.RequestException as e:
            print(f"[izsoles] page {pg} fetch failed: {e}")
            break
        if not pg_items:
            break
        link_items.extend(pg_items)

    # De-dup by URL across pages.
    seen = set()
    unique_items = []
    for it in link_items:
        if it["url"] not in seen:
            seen.add(it["url"])
            unique_items.append(it)

    # Fetch each detail page (rate-limited, capped).
    listings = []
    today = date.today().isoformat()
    for it in unique_items[:config.IZSOLES_MAX_DETAILS]:
        listing = None
        try:
            rd = s.get(it["url"], timeout=config.IZSOLES_TIMEOUT)
            rd.encoding = "utf-8"
            listing = _parse_detail(rd.text, it["url"], it["title"])
        except requests.RequestException as e:
            print(f"[izsoles] detail fetch failed for {it['url']}: {e}")
        time.sleep(config.IZSOLES_DELAY)

        if listing is None:
            continue
        # Keep only auctions that haven't ended.
        if listing.get("auction_end") and listing["auction_end"] < today:
            continue
        listings.append(listing)

    print(f"[izsoles] {len(listings)} active Riga apartment auction(s)")
    return listings


if __name__ == "__main__":
    for it in scrape():
        print(f"  {it['title'][:50]} | {it['rooms']}r {it['area_m2']}m2 | "
              f"start {it['auction_start_price']} | "
              f"bid {it['auction_current_bid']} | ends {it['auction_end']} | "
              f"{it['url'][-16:]}")
