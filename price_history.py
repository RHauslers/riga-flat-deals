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
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

import config
import utils

# ---------------------------------------------------------------------------
# File paths — read config.PRICE_HISTORY_JSON at call time, not import
# time: a module-level alias would freeze the path before tests (or a
# helper script) can point config at a temp file.
# ---------------------------------------------------------------------------

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
def load_price_history():
    """Load the full price history cache. Returns dict keyed by listing key."""
    return utils.read_json(config.PRICE_HISTORY_JSON, {})


def save_price_history(data):
    # Compact JSON — the file is committed daily and pretty-printing was
    # adding ~35% dead whitespace (same reason car state went v2).
    utils.write_json(config.PRICE_HISTORY_JSON, data, indent=None)


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


def update_price_history(listings, today=None):
    """Update price history for a batch of listings.

    For each listing:
    1. Record today's price in our own tracking (always, for all sources).
    2. If it's an SS.com listing and we haven't fetched CenuMednieks data
       recently (or ever), fetch it now.

    Returns the updated price_history dict.
    """
    history = load_price_history()
    today = today or date.today().isoformat()
    n_fetched = 0
    n_tracked = 0

    for listing in listings:
        price = listing.get('price_eur')
        if not price or not listing.get('source') or not listing.get('id'):
            # no id -> the key would collapse every id-less listing into
            # one shared "source:None" entry
            continue
        key = utils.listing_key(listing)

        entry = history.get(key, {
            'cenumednieks': None,
            'our_tracking': [],
            'first_seen': None,
        })
        entry.setdefault('our_tracking', [])  # tolerate legacy entries

        # 1. Record today's price in our own tracking.
        #    - first_seen is set once (the first day we ever saw this listing)
        #      and NEVER overwritten, so days-on-market stays correct.
        #    - our_tracking appends a new entry only when the price CHANGES.
        #    - When the price is unchanged we update last_seen on the most
        #      recent tracking entry, but never touch entry[0].
        #    - For city24 listings, if the API provides old_price (the price
        #      before the current one), inject it as a synthetic earlier
        #      observation so we capture drops we missed before tracking.
        if entry.get('first_seen') is None:
            entry['first_seen'] = today
            # city24 old_price: inject as a synthetic prior observation
            old_price = listing.get('old_price')
            if old_price and float(old_price) > 0 and float(old_price) != float(price):
                # Use yesterday as the date (we don't know when it changed)
                yesterday = (date.fromisoformat(today)
                             - timedelta(days=1)).isoformat()
                entry['our_tracking'].append({'date': yesterday, 'price': float(old_price)})
                entry['city24_old_price'] = float(old_price)

        today_obs = {'date': today, 'price': float(price)}
        if not entry['our_tracking']:
            entry['our_tracking'].append(today_obs)
            n_tracked += 1
        else:
            # Legacy "p"/"d"-keyed rows decode too — flat_motivated already
            # tolerates them; a direct ['price'] index used to KeyError here.
            last_obs = entry['our_tracking'][-1] or {}
            last_price = last_obs.get('price', last_obs.get('p'))
            last_date = last_obs.get('date', last_obs.get('d'))
            if last_price != float(price):
                # Price changed — new observation
                entry['our_tracking'].append(today_obs)
                n_tracked += 1
            elif last_date != today:
                # Same price, new day — update the LAST entry's date only.
                # This is safe: our_tracking[0] is only touched when a price
                # change creates a new first entry, which can't happen here.
                last_obs['date'] = today

        # 2. Fetch CenuMednieks data for SS.com listings (weekly refresh).
        #    cenumednieks_attempt stamps every try — hits AND misses — so an
        #    ad the tracker doesn't know isn't re-fetched every single run
        #    (needs_refresh stays True on a miss and would hammer the site
        #    daily at CENU_DELAY seconds per ad).
        ss_id = _extract_ss_id(listing)
        if ss_id and config.PRICE_HISTORY_CENU_ENABLED:
            cached = entry.get('cenumednieks')
            needs_refresh = (
                not cached
                or utils.older_than_days(cached.get('fetched_at'),
                                       config.CENU_REFRESH_DAYS)
            )
            due = utils.older_than_days(entry.get('cenumednieks_attempt'),
                                      config.CENU_REFRESH_DAYS)
            if needs_refresh and due:
                entry['cenumednieks_attempt'] = today
                cenu_data = fetch_cenumednieks(ss_id)
                if cenu_data:
                    entry['cenumednieks'] = cenu_data
                    n_fetched += 1
                time.sleep(CENU_DELAY)  # be respectful

        history[key] = entry

    # Prune entries whose newest observed activity is older than
    # PRICE_HISTORY_KEEP_DAYS — dead listings were accumulating forever.
    cutoff = (date.fromisoformat(today)
              - timedelta(days=config.PRICE_HISTORY_KEEP_DAYS)).isoformat()
    stale = [k for k, e in history.items()
             if (_entry_last_activity(e) or "") < cutoff]
    for k in stale:
        del history[k]
    if stale:
        print(f"[price_history] pruned {len(stale)} stale entries "
              f"(>{config.PRICE_HISTORY_KEEP_DAYS}d inactive)")

    if n_fetched or n_tracked or stale:
        save_price_history(history)
    if n_fetched or n_tracked:
        print(f"[price_history] tracked {n_tracked} price observations, "
              f"fetched {n_fetched} CenuMednieks lookups")

    return history


def _entry_last_activity(entry):
    """Newest date we have on record for a listing — used to prune entries
    that haven't been seen for a long time."""
    dates = [str(o.get('date') or o.get('d'))
             for o in entry.get('our_tracking') or []
             if o.get('date') or o.get('d')]
    cenu = entry.get('cenumednieks') or {}
    if cenu.get('fetched_at'):
        dates.append(str(cenu['fetched_at']))
    if entry.get('first_seen'):
        dates.append(str(entry['first_seen']))
    return max(dates) if dates else None


# ---------------------------------------------------------------------------
# Timeline formatting (for digest/website)
# ---------------------------------------------------------------------------
def get_price_timeline(listing, history=None):
    """Return a structured price timeline for a listing.

    Merges CenuMednieks historical data with our own daily observations.
    Returns a list of {date, price, source} sorted by date ascending,
    or None if no history exists.

    Note: 'previous ad' entries from CenuMednieks are older ads at the same
    address — they are NOT the same listing. They are included for context
    but tagged with source='CenuMednieks (previous ad)' so the formatter
    can separate them from the current ad's price history.
    """
    key = utils.listing_key(listing)
    if history is None:
        history = load_price_history()
    entry = history.get(key)
    if not entry:
        return None

    current_price = utils.to_float(listing.get('price_eur'), 0.0)
    timeline = []

    # CenuMednieks data (historical)
    cenu = entry.get('cenumednieks')
    if cenu:
        if cenu.get('first_listed_date') and cenu.get('original_price'):
            orig = utils.to_float(cenu['original_price'], 0.0)
            # Sanity check: if original_price is wildly different from the
            # current price (>5x or <1/5), it's likely from a different deal
            # type (e.g. a 275k sale price showing up for a 1.1k rental).
            # Skip it rather than showing a misleading -99.6% "drop".
            if current_price > 0 and orig > 0:
                ratio = max(orig, current_price) / min(orig, current_price)
                if ratio <= 5.0:
                    timeline.append({
                        'date': cenu['first_listed_date'],
                        'price': cenu['original_price'],
                        'source': 'CenuMednieks (original)',
                    })
        # Previous listings from same address (context only — different ads).
        # Also filter by price ratio to exclude cross-deal-type noise.
        for prev in cenu.get('previous_listings', []):
            prev_price = utils.to_float(prev.get('price'), 0.0)
            if current_price > 0 and prev_price > 0:
                ratio = max(prev_price, current_price) / min(prev_price, current_price)
                if ratio > 5.0:
                    continue
            timeline.append({
                'date': prev['date'],
                'price': prev['price'],
                'source': 'CenuMednieks (previous ad)',
            })

    # Our own tracking (daily observations) — tolerate legacy "p"/"d" keys.
    for obs in entry.get('our_tracking', []):
        obs_date = obs.get('date') or obs.get('d')
        obs_price = obs.get('price', obs.get('p'))
        if not obs_date or obs_price is None:
            continue
        timeline.append({
            'date': obs_date,
            'price': obs_price,
            'source': 'Flat_Searcher',
        })

    # Sort by date, deduplicate by date (keep last price per date —
    # dict assignment overwrites the earlier same-date entry, and the
    # ascending sort makes "last" the freshest).
    timeline.sort(key=lambda x: x['date'])
    timeline = list({t['date']: t for t in timeline}.values())

    return timeline if timeline else None


def format_price_timeline_html(listing, history=None):
    """Return an HTML snippet showing the price timeline for a listing.

    Shows even for single-observation listings (just "First seen: X EUR (date)").
    Every price point includes its date so the viewer can see how stale the
    listing is and how long between price changes.

    Previous CenuMednieks ads (different listings at the same address) are
    shown in a separate "Previous ads at this address" section, not mixed
    into the current ad's price history.
    """
    timeline = get_price_timeline(listing, history)
    if not timeline:
        return ''

    # Split into current-ad history and previous-ad context
    current_timeline = [t for t in timeline
                        if t['source'] != 'CenuMednieks (previous ad)']
    previous_ads = [t for t in timeline
                    if t['source'] == 'CenuMednieks (previous ad)']

    # Build current ad timeline.
    # If there's only one observation and the price never changed, show a
    # compact "Listed at X EUR (date), unchanged" instead of "First: X → X".
    # For multiple observations, skip no-op arrows (price unchanged between
    # observations) to avoid implying movement where there is none.
    parts = []
    if len(current_timeline) == 1:
        t = current_timeline[0]
        parts.append(f'<span style="color:var(--muted);font-size:11px">'
                     f'Listed at <b>{t["price"]:,.0f} EUR</b> ({t["date"]}), '
                     f'unchanged</span>')
    else:
        for i, t in enumerate(current_timeline):
            price = t['price']
            date_str = t['date']
            if i == 0:
                parts.append(f'<span style="color:var(--muted);font-size:11px">'
                            f'First: <b>{price:,.0f} EUR</b> ({date_str})</span>')
            else:
                prev_price = current_timeline[i - 1]['price']
                if price != prev_price:
                    pct = ((price - prev_price) / prev_price) * 100
                    # Up = bad for the buyer (red), down = good (green).
                    color = 'var(--bad)' if price > prev_price else 'var(--good)'
                    arrow = '↑' if price > prev_price else '↓'
                    parts.append(f' &rarr; <span style="color:{color};font-size:11px">'
                                f'<b>{price:,.0f} EUR</b> ({date_str}) '
                                f'{arrow}{abs(pct):.1f}%</span>')
                # Skip no-op arrows: if price is unchanged, don't render
                # a redundant "→ 250 EUR (date)" entry.

    # Build previous ads section (if any).
    # Show only the 3 most recent; collapse older ones into "(+N earlier)".
    prev_html = ''
    if previous_ads:
        previous_ads_sorted = sorted(previous_ads, key=lambda x: x['date'],
                                     reverse=True)
        shown = previous_ads_sorted[:3]
        hidden_count = len(previous_ads_sorted) - 3
        prev_parts = []
        for t in shown:
            prev_parts.append(
                f'<span style="color:var(--faint);font-size:10px">'
                f'{t["price"]:,.0f} EUR ({t["date"]})</span>'
            )
        prev_str = " &middot; ".join(prev_parts)
        if hidden_count > 0:
            prev_str += (f' <span style="color:var(--faint);font-size:10px">'
                         f'(+{hidden_count} earlier)</span>')
        prev_html = (f'<div style="margin:3px 0 0 0;padding-top:3px;'
                     f'border-top:1px dotted var(--line)">'
                     f'<span style="color:var(--faint);font-size:10px">'
                     f'Previous ads at this address:</span> '
                     f'{prev_str}</div>')

    # Add "days on market" from CenuMednieks if available
    key = utils.listing_key(listing)
    if history is None:
        history = load_price_history()
    entry = history.get(key, {})
    cenu = entry.get('cenumednieks')
    days_market = cenu.get('days_on_market') if cenu else None

    header = ''
    if days_market is not None and days_market > 0:
        header = (f'<div style="font-size:11px;color:var(--muted);margin:2px 0">'
                  f'On market: <b>{days_market} days</b></div>')

    spark = utils.sparkline_svg(
        [(t['date'], t['price']) for t in current_timeline])

    timeline_html = '<div style="margin:2px 0">' + ''.join(parts) + \
        spark + '</div>'
    return header + timeline_html + prev_html

