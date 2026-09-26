import math
import re
import statistics
import unicodedata
from datetime import date

import config


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def canonical_make(raw):
    value = unicodedata.normalize("NFKD", str(raw or "").lower())
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return {"vw": "volkswagen", "mercedes-benz": "mercedes"}.get(value, value)


def canonical_model(make, raw, year):
    value = unicodedata.normalize("NFKD", str(raw or "").lower())
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = 0
    if canonical_make(make) == "volkswagen" and value == "passat":
        if 2005 <= year <= 2009:
            return "passat-b6"
        if 2011 <= year <= 2014:
            return "passat-b7"
        if 2016 <= year <= 2022:
            return "passat-b8"
    if canonical_make(make) == "volkswagen" and value == "golf":
        if 2004 <= year <= 2007:
            return "golf-5"
        if 2009 <= year <= 2011:
            return "golf-6"
        if 2013 <= year <= 2018:
            return "golf-7"
    return value


def eligible(listing, max_price=None):
    price = _number(listing.get("price_eur"))
    year = _number(listing.get("year"))
    mileage = _number(listing.get("mileage_km"))
    engine = _number(listing.get("engine_l"))
    ceiling = config.CAR_COMPARABLE_MAX_PRICE_EUR if max_price is None else max_price
    return (price is not None and config.CAR_MIN_PRICE_EUR <= price <= ceiling
            and year is not None and year.is_integer()
            and config.CAR_MIN_YEAR <= year <= date.today().year + 1
            and mileage is not None and 0 <= mileage <= config.CAR_MAX_MILEAGE_KM
            and bool(listing.get("make")) and bool(listing.get("model"))
            and listing.get("fuel") in ("petrol", "diesel", "hybrid", "electric", "lpg")
            and (listing.get("fuel") == "electric" or (engine is not None and engine > 0)))


def _same_car(a, b):
    if a.get("source") == b.get("source"):
        return False
    if any(a.get(field) != b.get(field) for field in ("make", "model", "year", "fuel")):
        return False
    a_price, b_price = _number(a.get("price_eur")), _number(b.get("price_eur"))
    a_mileage, b_mileage = _number(a.get("mileage_km")), _number(b.get("mileage_km"))
    a_engine, b_engine = _number(a.get("engine_l")), _number(b.get("engine_l"))
    if any(value is None for value in (a_price, b_price, a_mileage, b_mileage,
                                       a_engine, b_engine)):
        return False
    if abs(a_price - b_price) > config.CAR_DEDUPE_PRICE_TOLERANCE_EUR:
        return False
    if abs(a_mileage - b_mileage) > config.CAR_DEDUPE_MILEAGE_TOLERANCE_KM:
        return False
    if abs(a_engine - b_engine) > config.CAR_DEDUPE_ENGINE_TOLERANCE_L:
        return False
    for field in ("gearbox", "body"):
        if a.get(field) and b.get(field) and a[field] != b[field]:
            return False
    if not a.get("gearbox") or not b.get("gearbox") or not a.get("body") or not b.get("body"):
        return (a_price == b_price and abs(a_mileage - b_mileage)
                <= config.CAR_DEDUPE_MISSING_SPECS_MILEAGE_TOLERANCE_KM)
    return True


def dedupe_cross_source(listings):
    result = []
    seen_ids = set()
    merged = 0
    for item in listings:
        key = (item.get("source"), str(item.get("id", "")))
        if not key[0] or not key[1] or key in seen_ids:
            continue
        seen_ids.add(key)
        listing = dict(item)
        for index, current in enumerate(result):
            sources = {current["source"]} | {entry["source"] for entry in current.get("also_on", [])}
            if listing["source"] in sources or not _same_car(current, listing):
                continue
            current_link = {field: current.get(field) for field in ("source", "url", "price_eur")}
            new_link = {field: listing.get(field) for field in ("source", "url", "price_eur")}
            if listing["price_eur"] < current["price_eur"]:
                listing["also_on"] = current.get("also_on", []) + [current_link]
                result[index] = listing
            else:
                current.setdefault("also_on", []).append(new_link)
            merged += 1
            break
        else:
            result.append(listing)
    return result, merged


def _comparable(target, peer):
    if target.get("source") == peer.get("source") and target.get("id") == peer.get("id"):
        return False
    if any(target.get(field) != peer.get(field) for field in ("make", "model", "fuel")):
        return False
    if abs(target["year"] - peer["year"]) > config.CAR_YEAR_TOLERANCE:
        return False
    if abs(target["mileage_km"] - peer["mileage_km"]) > config.CAR_MILEAGE_TOLERANCE_KM:
        return False
    for field in ("gearbox", "body"):
        if target.get(field) and peer.get(field) and target[field] != peer[field]:
            return False
    target_engine = _number(target.get("engine_l"))
    peer_engine = _number(peer.get("engine_l"))
    if target_engine is None or peer_engine is None:
        return target.get("fuel") == "electric" and peer.get("fuel") == "electric"
    return abs(target_engine - peer_engine) <= config.CAR_ENGINE_TOLERANCE_L


def score_and_rank(listings):
    market = [item for item in listings if eligible(item)]
    assessed = []
    for listing in market:
        if not eligible(listing, config.CAR_PRICE_CEILING_EUR):
            continue
        peers = [item for item in market if _comparable(listing, item)]
        scored = dict(listing)
        scored["_comps"] = len(peers)
        scored["_median"] = None
        scored["_discount_pct"] = None
        scored["_savings"] = None
        scored["_score"] = None
        scored["_good"] = False
        if len(peers) >= config.CAR_MIN_COMPARABLES:
            median = statistics.median(item["price_eur"] for item in peers)
            savings = median - listing["price_eur"]
            discount = 100 * savings / median
            scored["_median"] = median
            scored["_discount_pct"] = round(discount, 1)
            scored["_savings"] = savings
            scored["_score"] = max(0, min(config.CAR_SCORE_MAX, round(
                config.CAR_SCORE_CENTER + config.CAR_SCORE_DISCOUNT_MULTIPLIER * discount)))
            scored["_good"] = (discount >= config.CAR_GOOD_MIN_DISCOUNT_PCT
                               and savings >= config.CAR_GOOD_MIN_SAVINGS_EUR)
        assessed.append(scored)
    qualified = sorted((item for item in assessed if item["_good"]),
                       key=lambda item: (item["_score"], item["_savings"],
                                         -item["price_eur"]), reverse=True)
    return qualified, assessed
