# -*- coding: utf-8 -*-
"""
ss.com car scraper (Latvian section only).

Compliance notes:
  - robots.txt allows /lv/ but disallows /en/ and the sort URLs, so every
    request below uses /lv/ paths exclusively.
  - Seller ads only: we scrape /sell/ listing pages and additionally skip
    rows whose title starts with a want-to-buy/exchange/rent word.
  - We fetch only list pages — never ad detail pages, phone numbers or
    images. On HTTP 403/429 we stop immediately without retrying.

Row structure (verified):
  Each listing is <tr id="tr_{numeric_id}">. The title link is
  td.msg2 a.am with href /msg/lv/transport/cars/<make>/<model>/<slug>.html.
  Descriptive cells follow in order: year, engine, mileage, price with
  td classes msga2-o / msga2-r, e.g. "2011 | 2.0D | 254 tūkst. | 5,350 €".

Pagination (verified): page 2+ lives at "<list_url>pageN.html"; "?page=N"
repeats the first page's ads and must not be used.
"""
import re
import time

import requests
from bs4 import BeautifulSoup

import config
import car_value
import utils

MAKE_LINK_RE = re.compile(r"^/lv/transport/cars/([a-z0-9-]+)/sell/$")
NON_MAKE_SLUGS = {"new", "search", "exchange", "sell"}
AD_HREF_RE = re.compile(r"/transport/cars/([a-z0-9-]+)/([a-z0-9-]+)/[^/]+\.html")
MODEL_HREF_RE = re.compile(r"/msg/lv/transport/cars/([a-z0-9-]+)/([a-z0-9-]+)/")
BUY_TITLE_RE = re.compile(r"^\s*(pērk\w*|pirks\w*|mainu\b|maina\b|maiņ\w*|izīr\w*)", re.I)
TITLE_LPG_RE = re.compile(
    r"\b(?:benzin\w*\s*(?:\+|/|un)\s*gaz\w*|gaz\w*\s*(?:\+|/|un)\s*benzin\w*|lpg)\b",
    re.I,
)

GEARBOX_PATTERNS = (
    (re.compile(r"\b(automat\w*|automatic|dsg)\b", re.I), "automatic"),
    (re.compile(r"\b(mehan\w*|manual\w*)\b", re.I), "manual"),
)
BODY_PATTERNS = (
    (re.compile(r"\buniversal\w*\b", re.I), "wagon"),
    (re.compile(r"\bsedan\w*\b", re.I), "sedan"),
    (re.compile(r"\b(hecbek\w*|hatchback)\b", re.I), "hatchback"),
    (re.compile(r"\b(kupej\w*|coupe)\b", re.I), "coupe"),
    (re.compile(r"\bkabriolet\w*\b", re.I), "convertible"),
    (re.compile(r"\b(miniven\w*|mikroautobus\w*)\b", re.I), "minivan"),
    (re.compile(r"\b(apvid\w*|visurg\w*|dzip\w*|jeep|suv)\b", re.I), "suv"),
    (re.compile(r"\bpikap\w*\b", re.I), "pickup"),
)

_last_request_ts = [0.0]


class SourceBlocked(RuntimeError):
    """Raised when the site answers 403/429 — abort the scrape at once."""


def _fetch(url):
    elapsed = time.monotonic() - _last_request_ts[0]
    wait = max(1.0, config.CAR_SS_REQUEST_DELAY_SECONDS) - elapsed
    if wait > 0:
        time.sleep(wait)
    r = requests.get(
        url,
        headers={"User-Agent": config.SS_COM_USER_AGENT,
                 "Accept-Language": "lv,en;q=0.8"},
        timeout=config.CAR_SOURCE_TIMEOUT_SECONDS,
    )
    _last_request_ts[0] = time.monotonic()
    if r.status_code in (403, 429):
        raise SourceBlocked(f"HTTP {r.status_code} for {url}")
    r.encoding = "utf-8"
    r.raise_for_status()
    return r.text


def _parse_mileage(text):
    """'254 tūkst.' -> 254000 ; '254,5 tūkst.' -> 254500 ;
    '123 456' -> 123456 ; '-' -> None."""
    t = (text or "").strip()
    if not t or t in ("-", "—"):
        return None
    low = utils.strip_diacritics(t).lower()
    m = re.search(r"(\d[\d\s.,]*)", t)
    if not m:
        return None
    raw = re.sub(r"\s", "", m.group(1))
    if "tukst" in low:
        if re.fullmatch(r"\d+[.,]\d", raw):
            return int(round(float(raw.replace(",", ".")) * 1000))
        digits = re.sub(r"\D", "", raw)
        return int(digits) * 1000 if digits else None
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def _parse_engine(text):
    """'2.0D' -> (2.0, 'diesel') ; '1.6' -> (1.6, 'petrol') ;
    explicit hybrid/electric/gas tokens win over the plain-number rule."""
    t = utils.strip_diacritics(text or "").lower()
    m = re.search(r"\d+(?:[.,]\d+)?", t)
    engine = float(m.group(0).replace(",", ".")) if m else None
    if re.search(r"hibr|hybrid", t) or re.search(r"\d\s*h\b", t):
        fuel = "hybrid"
    elif re.search(r"elektr", t) or re.search(r"\d\s*e\b", t):
        fuel = "electric"
    elif re.search(r"gaze|lpg", t) or re.search(r"[/\s]g\b", t) or re.search(r"\dg\b", t):
        fuel = "lpg"
    elif re.search(r"\d\s*d\b", t) or t.rstrip().endswith("d"):
        fuel = "diesel"
    elif engine is not None:
        fuel = "petrol"
    else:
        fuel = None
    return engine, fuel


def _parse_price(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _title_specs(title):
    """Gearbox/body are taken from the ad title only when it states them
    clearly; otherwise None (unknown beats guessed)."""
    low = utils.strip_diacritics(title or "").lower()
    gearbox = body = None
    for rx, val in GEARBOX_PATTERNS:
        if rx.search(low):
            gearbox = val
            break
    for rx, val in BODY_PATTERNS:
        if rx.search(low):
            body = val
            break
    return gearbox, body


def _row_to_listing(tr):
    """Convert one <tr> car row into the unified car dict, or None when it
    is a non-seller row or carries no usable ad link."""
    tr_id = tr.get("id", "")
    m = re.match(r"tr_(\d+)", tr_id)
    if not m:
        return None
    a = tr.select_one("td.msg2 a.am") or tr.select_one("a[href^='/msg/']")
    if not a:
        return None
    href = a.get("href", "")
    mm = AD_HREF_RE.search(href)
    if not mm:
        return None
    title = a.get_text(" ", strip=True)
    if BUY_TITLE_RE.search(title):
        return None

    cells = tr.find_all("td", class_=re.compile(r"msga2-[or]"))
    if len(cells) < 4:
        return None
    texts = [c.get_text(" ", strip=True) for c in cells]
    year_text, engine_text, mileage_text, price_text = texts[0], texts[1], texts[2], texts[3]

    ym = re.search(r"\d{4}", year_text)
    year = int(ym.group(0)) if ym else None
    engine, fuel = _parse_engine(engine_text)
    if fuel == "petrol" and TITLE_LPG_RE.search(utils.strip_diacritics(title).lower()):
        fuel = "lpg"
    mileage = _parse_mileage(mileage_text)
    price = _parse_price(price_text)
    gearbox, body = _title_specs(title)

    make = car_value.canonical_make(mm.group(1))
    model = car_value.canonical_model(make, mm.group(2), year)
    url = config.CAR_SS_BASE + href if href.startswith("/") else href

    return {
        "source": "ss.com",
        "id": m.group(1),
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
        "location": "",
    }


def _scrape_pages(start_url, cap, results, seen_ids, model_counts=None):
    """Walk at most `cap` list pages starting at start_url. When
    model_counts is given, tally (make, model) URL slugs seen in ad links —
    used to rank which model pages deserve a deeper scan. Stops as soon as
    a page adds nothing new: ss.com repeats page 1 for page numbers beyond
    the last real page, so 'no new ids' means 'end of listings'."""
    for page_no in range(1, cap + 1):
        url = start_url if page_no == 1 else f"{start_url}page{page_no}.html"
        try:
            html = _fetch(url)
        except SourceBlocked:
            raise
        except requests.RequestException as e:
            print(f"[car ss.com] fetch failed for {url}: {e}")
            break
        if model_counts is not None:
            for make, model in MODEL_HREF_RE.findall(html):
                key = (make, model)
                model_counts[key] = model_counts.get(key, 0) + 1
        soup = BeautifulSoup(html, "lxml")
        new_on_page = 0
        for tr in soup.select("tr[id^='tr_']"):
            item = _row_to_listing(tr)
            if item and item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                results.append(item)
                new_on_page += 1
        if new_on_page == 0:
            break


def _discover_makes():
    """Return make slugs linked from the cars index page
    (links matching exactly /lv/transport/cars/<make>/sell/)."""
    html = _fetch(config.CAR_SS_MAKES_URL)
    soup = BeautifulSoup(html, "lxml")
    makes = []
    for a in soup.find_all("a", href=True):
        m = MAKE_LINK_RE.match(a["href"])
        if m and m.group(1) not in NON_MAKE_SLUGS and m.group(1) not in makes:
            makes.append(m.group(1))
    if not makes:
        raise RuntimeError("no make links found on the cars index page")
    return makes


def scrape(max_pages_per_make=None, max_pages_per_model=None, max_models=None):
    """Return list of car listing dicts from ss.com /lv/ car pages.

    Coverage is model-blind: scan the newest pages of every make (which
    also reveals which models currently have ads and in what volume),
    then deep-scan each observed model's own listing pages so every model
    gets a comparable-price pool deep enough to score. No model is
    special-cased.

    max_pages_per_make: override config.CAR_SS_MAX_PAGES_PER_MAKE (0
        disables the per-make scan, and with it model discovery).
    max_pages_per_model: override config.CAR_SS_MAX_PAGES_PER_MODEL (0
        disables the per-model deep scan).
    max_models: override config.CAR_SS_MAX_MODELS — safety bound on how
        many model pages are deep-scanned; models are ranked by observed
        ad volume (most first) so the bound drops only the quietest
        models.
    """
    cap_make = config.CAR_SS_MAX_PAGES_PER_MAKE if max_pages_per_make is None else max_pages_per_make
    cap_model = config.CAR_SS_MAX_PAGES_PER_MODEL if max_pages_per_model is None else max_pages_per_model
    cap_models = config.CAR_SS_MAX_MODELS if max_models is None else max_models
    results = []
    seen_ids = set()
    model_counts = {}

    if cap_make:
        for make in _discover_makes():
            url = f"{config.CAR_SS_BASE}/lv/transport/cars/{make}/sell/"
            _scrape_pages(url, cap_make, results, seen_ids, model_counts)

    if cap_model and model_counts:
        ranked = sorted(model_counts.items(),
                        key=lambda kv: (-kv[1], kv[0]))[:max(0, cap_models)]
        for (make, model), _count in ranked:
            url = f"{config.CAR_SS_BASE}/lv/transport/cars/{make}/{model}/sell/"
            _scrape_pages(url, cap_model, results, seen_ids)
        skipped = len(model_counts) - len(ranked)
        if skipped > 0:
            print(f"[car ss.com] model deep-scan skipped {skipped} "
                  f"lower-volume model(s) (cap {cap_models})")
        print(f"[car ss.com] deep-scanned {len(ranked)} model page(s) "
              f"of {len(model_counts)} observed")

    print(f"[car ss.com] {len(results)} seller listings scraped")
    return results
