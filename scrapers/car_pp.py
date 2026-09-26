# -*- coding: utf-8 -*-
"""
pp.lv car scraper (Playwright, headless Chromium).

Compliance notes:
  - robots.txt: Allow: / with Crawl-delay: 5 — we wait >= 5 s between page
    navigations and never click cookie consent or log in.
  - Only the public search list is read (config.CAR_PP_LIST_URL, pages via
    &page=N). No ad detail pages, no personal data, no images downloaded.

Card structure (verified):
  Each result is an <a href=".../<make>/<model>/<slug>/!<id>"> card.
  Seller cards have an <img alt="Pārdod - ...">; "Pērk", "Maiņa", "Izīrē"
  and unknown prefixes are skipped.
  Specs sit in titled divs: div[title='Degvielas tips'] (fuel),
  div[title='Motora tilpums'] (engine L), div[title='Virsbūves tips']
  (body), div[title='Ātrumkārba'] (gearbox), div[title='Izlaiduma gads']
  (year), div[title='Nobraukums, km'] (mileage); the price is
  .grid__view__price div.me-auto and uses thin spaces (U+2009).
"""
import re

from bs4 import BeautifulSoup

import config
import car_value
import utils

CARD_SELECTOR = "a[href*='/!']"
PP_BASE = "https://pp.lv"

FUEL_MAP = (("hibrid", "hybrid"), ("elektr", "electric"), ("gaze", "lpg"),
            ("lpg", "lpg"), ("dizel", "diesel"), ("benzin", "petrol"))
GEARBOX_MAP = (
    ("mehan", "manual"),
    ("manual", "manual"),
    ("automat", "automatic"),
)
BODY_MAP = (
    ("universal", "wagon"),
    ("sedan", "sedan"),
    ("hecbek", "hatchback"),
    ("kupej", "coupe"),
    ("kabriolet", "convertible"),
    ("miniven", "minivan"),
    ("mikroautobus", "minivan"),
    ("apvid", "suv"),
    ("visurg", "suv"),
    ("dzips", "suv"),
    ("jeep", "suv"),
    ("pikap", "pickup"),
)


def _norm(text):
    return utils.strip_diacritics(text or "").lower().strip()


def _mapped(text, table):
    low = _norm(text)
    for key, val in table:
        if key in low:
            return val
    return None


def _to_int(text):
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def _to_float(text):
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def _spec(card, title):
    """Text of the spec div whose tooltip is `title`, e.g. 'Degvielas tips'."""
    el = card.find("div", attrs={"title": title})
    return el.get_text(" ", strip=True) if el else ""


def _card_to_listing(a):
    """Convert one pp.lv card <a> into the unified car dict, or None when it
    is not a seller card or has no usable ad id/link."""
    href = a.get("href", "")
    mid = re.search(r"!(\d+)", href)
    if not mid:
        return None

    img = a.find("img")
    alt = _norm(img.get("alt", "") if img else "")
    if not alt.startswith("pardod"):
        return None

    segs = [s for s in href.split("/") if s]
    bang = next((i for i, s in enumerate(segs) if s.startswith("!")), len(segs))
    try:
        cat = segs.index("vieglie-auto")
    except ValueError:
        cat = max(bang - 3, -1)
    make_seg = segs[cat + 1] if cat + 1 < bang else ""
    model_seg = segs[bang - 1] if bang >= 1 else ""
    if model_seg == make_seg:
        model_seg = ""

    title_el = a.select_one("h3[data-test='classified-title']") or a.find("h3")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    price_el = a.select_one(".grid__view__price .me-auto")
    price = _to_int(price_el.get_text(" ", strip=True)) if price_el else None

    year = _to_int(_spec(a, "Izlaiduma gads"))
    mileage = _to_int(_spec(a, "Nobraukums, km"))
    engine = _to_float(_spec(a, "Motora tilpums"))
    fuel = _mapped(_spec(a, "Degvielas tips"), FUEL_MAP)
    gearbox = _mapped(_spec(a, "Ātrumkārba"), GEARBOX_MAP)
    body = _mapped(_spec(a, "Virsbūves tips"), BODY_MAP)

    loc_el = a.find("div", attrs={"title": "Atrašanās vieta"}) or \
        a.select_one("[data-test*='location'],[data-test*='region']")
    location = loc_el.get_text(" ", strip=True) if loc_el else ""

    make = car_value.canonical_make(make_seg)
    model = car_value.canonical_model(make, model_seg, year)
    url = PP_BASE + href if href.startswith("/") else href

    return {
        "source": "pp.lv",
        "id": mid.group(1),
        "url": url,
        "make": make,
        "model": model,
        "year": year,
        "mileage_km": mileage,
        "fuel": fuel,
        "engine_l": engine,
        "gearbox": gearbox,
        "body": body,
        "price_eur": price,
        "title": title,
        "location": location,
    }


def parse_page(html):
    """Parse one rendered results page into unified car dicts."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    seen = set()
    for a in soup.select(CARD_SELECTOR):
        item = _card_to_listing(a)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def scrape(max_pages=None):
    """Return list of car listing dicts from pp.lv vieglie-auto search."""
    cap = config.CAR_PP_MAX_PAGES if max_pages is None else max_pages
    if cap <= 0:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright is not installed")

    results = []
    seen = set()
    delay_ms = int(max(5.0, config.CAR_PP_REQUEST_DELAY_SECONDS) * 1000)
    nav_timeout = config.CAR_SOURCE_TIMEOUT_SECONDS * 1000

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=config.SS_COM_USER_AGENT)
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_())
            for pg in range(1, cap + 1):
                if pg > 1:
                    page.wait_for_timeout(delay_ms)
                url = config.CAR_PP_LIST_URL if pg == 1 else f"{config.CAR_PP_LIST_URL}&page={pg}"
                page.goto(url, timeout=nav_timeout, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector(CARD_SELECTOR, timeout=nav_timeout)
                except Exception:
                    pass
                items = parse_page(page.content())
                if pg == 1 and not items:
                    raise RuntimeError(
                        "pp.lv page 1 returned no seller cards — markup may "
                        "have changed or the site blocked the request")
                new_items = [i for i in items if i["id"] not in seen]
                for i in new_items:
                    seen.add(i["id"])
                results.extend(new_items)
                if not new_items:
                    break
        finally:
            browser.close()

    print(f"[car pp.lv] {len(results)} seller listings scraped")
    return results
