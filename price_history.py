# -*- coding: utf-8 -*-
"""
Price history tracking — two sources combined:

1. CenuMednieks.lv (historical backfill):
   Tracks SS.lv ad price history. Given an SS.com ad ID, fetches the original
   price, current price, price changes, and first-listed date — data we could
   never get from our own scraping because it predates our first run.
   URL pattern: https://cenumednieks.lv/ad/{ss_id}
   Free tier: original/current/last change + date. PRO: full timeline (locked).

2. Our own daily tracking (going forward):
   Each day we record the current price for every listing we scrape. Over time
   this builds our own price timeline that supplements CenuMednieks data.

Data is cached in data/price_history.json so we only query CenuMednieks once
per listing, then refresh weekly (CENU_REFRESH_DAYS). Our own observations are
appended every run.

Only SS.com listings can be enriched with CenuMednieks data (it tracks SS.lv
only). City24 listings get our own tracking only.
"""
import json
import os
import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

import config

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------
PRICE_HISTORY_JSON = os.path.join(config.DATA_DIR, "price_history.json")

# ---------------------------------------------------------------------------
# CenuMednieks.lv scraper
# ---------------------------------------------------------------------------
CENU_BASE = "https://cenumednieks.lv/ad/"
CENU_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CENU_TIMEOUT = 15
CENU_DELAY = 1.0  # seconds between requests (be respectful)


def _parse_price(text):
    """'550 €' -> 550, '55 000 €' -> 55000, '550 €/mēn.' -> 550, 'XX XXX €' -> None."""
    if not text:
        return None
    if 'XX' in text:
        return None
    # Remove everything except digits and spaces, then parse
    cleaned = re.sub(r'[^\d\s]', '', text)
    cleaned = cleaned.replace(' ', '').strip()
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_date(text):
    """'11.03.2026 13:00' -> '2026-03-11', '00.00.0000 00:00' -> None."""
    if not text or '00.00.0000' in text:
        return None
    try:
        dt = datetime.strptime(text.strip()[:10], '%d.%m.%Y')
        return dt.date().isoformat()
    except ValueError:
        return None


def fetch_cenumednieks(ss_id):
    """Fetch price history for an SS.com listing from CenuMednieks.lv.

    Returns dict with:
      original_price, current_price, total_change, first_listed_date,
      days_on_market, previous_listings (list of {date, price} for older
      ads from the same owner), fetched_at.

    Returns None if the page is not found or parsing fails.
    """
    url = f"{CENU_BASE}{ss_id}"
    try:
        r = requests.get(url, headers={'User-Agent': CENU_USER_AGENT},
                         timeout=CENU_TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(r.text, 'lxml')

    # Find all "Cenu hronoloģija" cards — one per listing block
    chronology_cards = soup.find_all('h5', string=re.compile('Cenu hronolo'))
    if not chronology_cards:
        return None

    # The first card is the current/most recent listing
    result = {
        'original_price': None,
        'current_price': None,
        'total_change': None,
        'first_listed_date': None,
        'days_on_market': None,
        'previous_listings': [],
        'fetched_at': date.today().isoformat(),
        'source_url': url,
    }

    for i, card_header in enumerate(chronology_cards):
        card_body = card_header.parent  # card-body div

        # Extract SĀKOTNĒJĀ / AKTUĀLĀ / IZMAIŅAS values
        labels = card_body.find_all('div', class_='small-xs')
        prices = card_body.find_all('div', class_='fw-bold')

        original = current = change = None
        for label in labels:
            txt = label.get_text(strip=True)
            price_div = label.find_next_sibling('div')
            if price_div:
                val = _parse_price(price_div.get_text(strip=True))
                if txt == 'SĀKOTNĒJĀ':
                    original = val
                elif txt == 'AKTUĀLĀ':
                    current = val
                elif txt == 'IZMAIŅAS':
                    change = val

        # Extract the date and price from the timeline entry
        date_div = card_body.find('div', class_='fw-bold small')
        listed_date = None
        listed_price = None
        if date_div:
            listed_date = _parse_date(date_div.get_text(strip=True))
            price_sibling = date_div.find_next_sibling('div')
            if price_sibling:
                listed_price = _parse_price(price_sibling.get_text(strip=True))

        if i == 0:
            # Current listing
            result['original_price'] = original
            result['current_price'] = current
            result['total_change'] = change
            result['first_listed_date'] = listed_date
            if listed_date:
                try:
                    d = datetime.strptime(listed_date, '%Y-%m-%d').date()
                    result['days_on_market'] = (date.today() - d).days
                except ValueError:
                    pass
        else:
            # Previous listing from same owner/address
            if listed_date and listed_price:
                result['previous_listings'].append({
                    'date': listed_date,
                    'price': listed_price,
                    'original_price': original,
                })

    return result


# ---------------------------------------------------------------------------
# Price history storage (data/price_history.json)
# ---------------------------------------------------------------------------
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


def load_price_history():
    """Load the full price history cache. Returns dict keyed by listing key."""
    return _read_json(PRICE_HISTORY_JSON, {})


def save_price_history(data):
    _write_json(PRICE_HISTORY_JSON, data)


def _listing_key(listing):
    return f"{listing.get('source')}:{listing.get('id')}"


def _extract_ss_id(listing):
    """Extract the SS.com ad slug for CenuMednieks lookup.

    SS.com ad URLs contain an alphabetic ID like 'ahgbe' — this is what
    CenuMednieks uses in its URL pattern: cenumednieks.lv/ad/ahgbe
    """
    if listing.get('source') != 'ss.com':
        return None
    # Prefer the ad_slug field (extracted from URL by the scraper)
    ad_slug = listing.get('ad_slug', '')
    if ad_slug and re.match(r'^[a-z]+$', str(ad_slug)):
        return str(ad_slug)
    # Fall back to trying the id field (older listings may not have ad_slug)
    ad_id = listing.get('id', '')
    if ad_id and re.match(r'^[a-z]+$', str(ad_id)):
        return str(ad_id)
    return None


def update_price_history(listings):
    """Update price history for a batch of listings.

    For each listing:
    1. Record today's price in our own tracking (always, for all sources).
    2. If it's an SS.com listing and we haven't fetched CenuMednieks data
       recently (or ever), fetch it now.

    Returns the updated price_history dict.
    """
    history = load_price_history()
    today = date.today().isoformat()
    n_fetched = 0
    n_tracked = 0

    for listing in listings:
        key = _listing_key(listing)
        price = listing.get('price_eur')
        if not price:
            continue

        entry = history.get(key, {
            'cenumednieks': None,
            'our_tracking': [],
        })

        # 1. Record today's price in our own tracking (skip duplicates)
        today_obs = {'date': today, 'price': float(price)}
        if not entry['our_tracking'] or entry['our_tracking'][-1]['price'] != float(price):
            entry['our_tracking'].append(today_obs)
            n_tracked += 1
        elif entry['our_tracking'] and entry['our_tracking'][-1]['date'] != today:
            # Same price but new day — update the date
            entry['our_tracking'][-1]['date'] = today

        # 2. Fetch CenuMednieks data for SS.com listings (weekly refresh)
        ss_id = _extract_ss_id(listing)
        if ss_id and config.PRICE_HISTORY_CENU_ENABLED:
            cached = entry.get('cenumednieks')
            needs_refresh = (
                not cached
                or _is_older_than_days(cached.get('fetched_at'),
                                       config.CENU_REFRESH_DAYS)
            )
            if needs_refresh:
                cenu_data = fetch_cenumednieks(ss_id)
                if cenu_data:
                    entry['cenumednieks'] = cenu_data
                    n_fetched += 1
                time.sleep(CENU_DELAY)  # be respectful

        history[key] = entry

    if n_fetched or n_tracked:
        save_price_history(history)
        print(f"[price_history] tracked {n_tracked} price observations, "
              f"fetched {n_fetched} CenuMednieks lookups")

    return history


def _is_older_than_days(date_str, days):
    if not date_str:
        return True
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d').date()
        return (date.today() - d).days >= days
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# Timeline formatting (for digest/website)
# ---------------------------------------------------------------------------
def get_price_timeline(listing, history=None):
    """Return a structured price timeline for a listing.

    Merges CenuMednieks historical data with our own daily observations.
    Returns a list of {date, price, source} sorted by date ascending,
    or None if no history exists.
    """
    key = _listing_key(listing)
    if history is None:
        history = load_price_history()
    entry = history.get(key)
    if not entry:
        return None

    timeline = []

    # CenuMednieks data (historical)
    cenu = entry.get('cenumednieks')
    if cenu:
        if cenu.get('first_listed_date') and cenu.get('original_price'):
            timeline.append({
                'date': cenu['first_listed_date'],
                'price': cenu['original_price'],
                'source': 'CenuMednieks (original)',
            })
        # Previous listings from same address (context)
        for prev in cenu.get('previous_listings', []):
            timeline.append({
                'date': prev['date'],
                'price': prev['price'],
                'source': 'CenuMednieks (previous ad)',
            })

    # Our own tracking (daily observations)
    for obs in entry.get('our_tracking', []):
        timeline.append({
            'date': obs['date'],
            'price': obs['price'],
            'source': 'Flat_Searcher',
        })

    # Sort by date, deduplicate by date (keep last price per date)
    timeline.sort(key=lambda x: x['date'])
    seen_dates = {}
    for t in timeline:
        seen_dates[t['date']] = t
    timeline = list(seen_dates.values())
    timeline.sort(key=lambda x: x['date'])

    return timeline if timeline else None


def format_price_timeline_html(listing, history=None):
    """Return an HTML snippet showing the price timeline for a listing.

    Shows even for single-observation listings (just "First seen: X EUR (date)").
    Every price point includes its date so the viewer can see how stale the
    listing is and how long between price changes.
    """
    timeline = get_price_timeline(listing, history)
    if not timeline:
        return ''

    # Build a compact timeline with dates on every entry
    parts = []
    for i, t in enumerate(timeline):
        price = t['price']
        date_str = t['date']
        if i == 0:
            parts.append(f'<span style="color:#888;font-size:11px">'
                        f'First: <b>{price:,.0f} EUR</b> ({date_str})</span>')
        else:
            prev_price = timeline[i - 1]['price']
            if price != prev_price:
                pct = ((price - prev_price) / prev_price) * 100
                color = '#e74c3c' if price > prev_price else '#27ae60'
                arrow = '↑' if price > prev_price else '↓'
                parts.append(f' &rarr; <span style="color:{color};font-size:11px">'
                            f'<b>{price:,.0f} EUR</b> ({date_str}) '
                            f'{arrow}{abs(pct):.1f}%</span>')
            else:
                parts.append(f' &rarr; <span style="color:#888;font-size:11px">'
                            f'{price:,.0f} EUR ({date_str})</span>')

    # Add "days on market" from CenuMednieks if available
    key = _listing_key(listing)
    if history is None:
        history = load_price_history()
    entry = history.get(key, {})
    cenu = entry.get('cenumednieks')
    days_market = cenu.get('days_on_market') if cenu else None

    header = ''
    if days_market is not None and days_market > 0:
        header = (f'<div style="font-size:11px;color:#666;margin:2px 0">'
                  f'On market: <b>{days_market} days</b></div>')

    timeline_html = '<div style="margin:2px 0">' + ''.join(parts) + '</div>'
    return header + timeline_html


def get_price_drop_info(listing, history=None):
    """Return a summary of price drops for a listing.

    Returns dict with: original_price, current_price, total_drop_pct,
    n_drops, first_date, days_on_market. Or None if no history.
    """
    timeline = get_price_timeline(listing, history)
    if not timeline or len(timeline) < 2:
        return None

    first = timeline[0]
    last = timeline[-1]
    drops = sum(1 for i in range(1, len(timeline))
                if timeline[i]['price'] < timeline[i - 1]['price'])

    total_drop = first['price'] - last['price']
    total_drop_pct = (total_drop / first['price']) * 100 if first['price'] else 0

    return {
        'original_price': first['price'],
        'current_price': last['price'],
        'total_drop': total_drop,
        'total_drop_pct': total_drop_pct,
        'n_drops': drops,
        'first_date': first['date'],
        'days_on_market': None,  # filled from CenuMednieks if available
    }
