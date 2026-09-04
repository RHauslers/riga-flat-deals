# -*- coding: utf-8 -*-
"""
ss.com scraper for Riga flats (today's listings).

Page structure (verified):
  Each listing is <tr id="tr_{numeric_id}"> with cells in this order:
    District ( "Name<br>Street" ), Rooms, m2, Floor ("x/y"), Series,
    Price/m2, Price.
  The ad detail link is the first <a href="/msg/.../riga/{district}/{slug}.html">.
  Highlighted rows wrap the cell text in <b>.

We fetch the "today" page for each deal type, parse every row, then keep only
rows whose district matches one of our target districts (config.DISTRICTS).
"""
import re
import requests
from bs4 import BeautifulSoup

import config


def _parse_price(text):
    """Parse European-formatted price strings -> float EUR.
       '1,163 €' -> 1163.0 ; '57,000 €' -> 57000.0 ; '350.00' -> 350.0
    """
    if not text:
        return None
    s = text.replace("\xa0", " ").replace("€", "").strip()
    s = re.sub(r"[^0-9.,]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        # both present -> comma is thousands separator
        s = s.replace(",", "")
    elif "," in s:
        # comma only: thousands if it matches \d{1,3}(,\d{3})+ else decimal
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_floor(text):
    """'3/4' -> (3, 4); '5' -> (5, None); 'ground' -> (0, None)."""
    if not text:
        return (None, None)
    t = text.strip().lower()
    m = re.search(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"\d+", t)
    if m:
        return (int(m.group(0)), None)
    return (None, None)


def _match_district(cell_text):
    """Return canonical district name if cell text matches a target, else None."""
    low = cell_text.lower()
    for canon, aliases in config.DISTRICTS.items():
        for alias in aliases:
            if alias in low:
                return canon
    return None


def _row_to_listing(tr, deal_type):
    """Convert one <tr> listing row into a unified dict, or None if unusable."""
    tr_id = tr.get("id", "")
    m = re.match(r"tr_(\d+)", tr_id)
    if not m:
        return None
    list_id = m.group(1)

    cells = tr.find_all("td", class_=re.compile(r"msga2-o|msg2"))
    # The descriptive cells (district, rooms, area, floor, series, price/m2, price)
    # are the ones with class containing "msga2-o". The title cell is class "msg2".
    desc_cells = tr.find_all("td", class_=re.compile(r"msga2-o"))
    if len(desc_cells) < 7:
        return None

    district_cell = desc_cells[0]
    district_text = district_cell.get_text(" ", strip=True)
    # district cell looks like "Bolderaya Plata 8" -> first token line is district
    # Use <br> to split district / street if present.
    district_html = district_cell.decode_contents()
    parts = re.split(r"<br\s*/?>", district_html, maxsplit=1)
    district_name_raw = BeautifulSoup(parts[0], "lxml").get_text(strip=True)
    street = BeautifulSoup(parts[1], "lxml").get_text(strip=True) if len(parts) > 1 else ""

    district = _match_district(district_name_raw)
    if district is None:
        return None  # not one of our target districts

    rooms = _to_int(desc_cells[1].get_text(strip=True))
    area = _to_float(desc_cells[2].get_text(strip=True))
    floor_text = desc_cells[3].get_text(strip=True)
    floor_num, floor_total = _parse_floor(floor_text)
    series = desc_cells[4].get_text(strip=True)
    price_per_m2 = _parse_price(desc_cells[5].get_text(strip=True))
    price = _parse_price(desc_cells[6].get_text(strip=True))

    # ad url + title from the message link
    a = tr.select_one("td.msg2 a.am") or tr.select_one("a[href^='/msg/']")
    url = title = ""
    if a:
        href = a.get("href", "")
        url = config.SS_COM_BASE + href if href.startswith("/") else href
        title = a.get_text(" ", strip=True)

    if price is None or area is None or area <= 0:
        return None

    return {
        "source": "ss.com",
        "deal_type": deal_type,
        "id": list_id,
        "url": url,
        "district": district,
        "street": street,
        "rooms": rooms,
        "area_m2": area,
        "floor": floor_text,
        "floor_num": floor_num,
        "floor_total": floor_total,
        "series": series,
        "price_eur": price,
        "price_per_m2": price_per_m2 if price_per_m2 is not None else round(price / area, 2),
        "title": title,
    }


def _to_int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def _to_float(text):
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def _fetch(url):
    r = requests.get(
        url,
        headers={"User-Agent": config.SS_COM_USER_AGENT,
                 "Accept-Language": "en-US,en;q=0.9"},
        timeout=config.SS_COM_TIMEOUT,
    )
    r.encoding = "utf-8"
    r.raise_for_status()
    return r.text


def _next_page_url(soup, base_url):
    """ss.com today pages are single-page; detect a 'next' anchor just in case."""
    a = soup.select_one("a.navi[href]:-soup-contains('»')")
    if not a:
        return None
    href = a.get("href", "")
    if href.startswith("/"):
        href = config.SS_COM_BASE + href
    return href or None


def scrape(deal_type):
    """Return list of listing dicts for the given deal_type ('rent'/'sale')."""
    if deal_type not in config.SS_COM_TODAY_URL:
        return []
    url = config.SS_COM_TODAY_URL[deal_type]
    results = []
    for _ in range(config.SS_COM_MAX_PAGES):
        try:
            html = _fetch(url)
        except requests.RequestException as e:
            print(f"[ss.com] fetch failed for {url}: {e}")
            break
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("tr[id^='tr_']")
        for tr in rows:
            item = _row_to_listing(tr, deal_type)
            if item:
                results.append(item)
        nxt = _next_page_url(soup, url)
        if not nxt or nxt == url:
            break
        url = nxt
    print(f"[ss.com] {deal_type}: {len(results)} listings in target districts")
    return results


if __name__ == "__main__":
    for dt in config.DEAL_TYPES:
        items = scrape(dt)
        for it in items[:5]:
            print(it["deal_type"], it["district"], it["rooms"], "r",
                  it["area_m2"], "m2", it["floor"], it["price_eur"], "EUR",
                  it["price_per_m2"], "EUR/m2", it["url"])
