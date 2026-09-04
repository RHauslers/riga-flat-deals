# -*- coding: utf-8 -*-
"""
Exceptional deal detection — surfaces the top 3-5 deals that stand out across
multiple signals, presented in a clean, easy-to-scan header at the top of the
digest.

Composite "exceptional score" combines:
  1. Deal score (how cheap vs model expectation) — from scoring.py
  2. Price drop % (from CenuMednieks/our tracking) — bigger drop = better
  3. Days on market (longer + price drop = more desperate seller)
  4. Price per m² vs district median (cheaper than neighbors = better)

The result is a short list of 3-5 deals with a one-line summary each,
color-coded by signal strength, so the recipient can see the best opportunities
at a glance without reading the full tables.
"""
from datetime import date
from statistics import median

import config
import price_history


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _get_price_drop_pct(listing, price_data):
    """Return price drop percentage (positive = price dropped)."""
    if not price_data:
        return 0.0
    key = f"{listing.get('source')}:{listing.get('id')}"
    entry = price_data.get(key, {})
    cenu = entry.get('cenumednieks')
    current = _safe_float(listing.get('price_eur'))

    # Prefer CenuMednieks original price
    if cenu and cenu.get('original_price') and current > 0:
        original = _safe_float(cenu['original_price'])
        if original > 0:
            return ((original - current) / original) * 100

    # Fall back to our own tracking
    our = entry.get('our_tracking', [])
    if len(our) >= 2 and current > 0:
        first = _safe_float(our[0].get('price'))
        if first > 0:
            return ((first - current) / first) * 100

    return 0.0


def _get_days_on_market(listing, price_data):
    """Return days on market (0 if unknown)."""
    if not price_data:
        return 0
    key = f"{listing.get('source')}:{listing.get('id')}"
    entry = price_data.get(key, {})
    cenu = entry.get('cenumednieks')
    if cenu and cenu.get('days_on_market') is not None:
        return cenu['days_on_market']

    our = entry.get('our_tracking', [])
    if our:
        try:
            d = date.fromisoformat(our[0].get('date', '')[:10])
            return (date.today() - d).days
        except ValueError:
            pass
    return 0


def _district_median_ppu(listings, district, deal_type):
    """Calculate median price per m² for a district+deal_type."""
    ppus = [_safe_float(l.get('price_per_m2'))
            for l in listings
            if l.get('district') == district and l.get('deal_type') == deal_type
            and _safe_float(l.get('price_per_m2')) > 0]
    return median(ppus) if ppus else 0.0


def compute_exceptional_scores(all_scored, all_listings, price_data):
    """Compute a composite exceptional score for each listing.

    all_scored: dict of deal_type -> [(listing, score, method), ...]
    all_listings: flat list of all listings (for district median calc)
    price_data: price history dict

    Returns list of (listing, exceptional_score, signals_dict) sorted desc.
    """
    results = []

    # Precompute district medians
    medians = {}
    for dt in config.DEAL_TYPES:
        for district in config.DISTRICTS:
            medians[(district, dt)] = _district_median_ppu(
                all_listings, district, dt
            )

    for dt, items in all_scored.items():
        for item in items:
            listing, deal_score, method = item[0], item[1], item[2]

            drop_pct = _get_price_drop_pct(listing, price_data)
            days = _get_days_on_market(listing, price_data)
            ppu = _safe_float(listing.get('price_per_m2'))
            district = listing.get('district', '')
            med_ppu = medians.get((district, dt), 0)

            # Price vs district median (negative = cheaper than median = good)
            ppu_discount = 0.0
            if med_ppu > 0 and ppu > 0:
                ppu_discount = ((med_ppu - ppu) / med_ppu) * 100

            # Composite score (weighted):
            #   deal_score:     weight 2.0 (model's assessment)
            #   drop_pct:       weight 1.5 (price dropped from original)
            #   ppu_discount:   weight 1.0 (cheaper than neighbors)
            #   days factor:    weight 0.5 (longer on market + has drop = desperate)
            #
            # Days factor only helps if there's also a price drop
            # (long time on market with no drop = overpriced, not a deal)
            days_factor = 0.0
            if drop_pct > 0 and days > 0:
                # Diminishing returns: 30 days = +0.5, 100 days = +1.0, 365 = +1.5
                days_factor = min(1.5, (days / 100) ** 0.5)

            exceptional = (
                _safe_float(deal_score) * 2.0
                + drop_pct * 0.15  # 10% drop = +1.5
                + ppu_discount * 0.10  # 10% cheaper than median = +1.0
                + days_factor * 0.5
            )

            signals = {
                'deal_score': deal_score,
                'drop_pct': drop_pct,
                'ppu_discount': ppu_discount,
                'days_on_market': days,
                'exceptional_score': exceptional,
            }

            results.append((listing, exceptional, signals))

    # Sort by exceptional score descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def build_exceptional_html(all_scored, all_listings, price_data, top_n=5):
    """Build the HTML for the exceptional deals header.

    Returns a clean, compact section with the top N deals, each as a
    one-line summary with color-coded badges.
    """
    scored = compute_exceptional_scores(all_scored, all_listings, price_data)
    if not scored:
        return ""

    top = scored[:top_n]

    cards = []
    for i, (listing, exc_score, signals) in enumerate(top):
        district = listing.get('district', '?')
        rooms = listing.get('rooms', '?')
        area = listing.get('area_m2', '?')
        floor = listing.get('floor', '?')
        price = _safe_float(listing.get('price_eur'))
        ppu = _safe_float(listing.get('price_per_m2'))
        deal_type = listing.get('deal_type', '?')
        url = listing.get('url', '')
        source = listing.get('source', '')

        # Build signal badges
        badges = []
        if signals['drop_pct'] > 0:
            badges.append(
                f'<span style="background:#27ae60;color:#fff;padding:2px 8px;'
                f'border-radius:10px;font-size:11px">'
                f'↓ {signals["drop_pct"]:.0f}% price drop</span>'
            )
        if signals['ppu_discount'] > 5:
            badges.append(
                f'<span style="background:#2980b9;color:#fff;padding:2px 8px;'
                f'border-radius:10px;font-size:11px">'
                f'{signals["ppu_discount"]:.0f}% below area avg</span>'
            )
        if signals['days_on_market'] > 30:
            badges.append(
                f'<span style="background:#8e44ad;color:#fff;padding:2px 8px;'
                f'border-radius:10px;font-size:11px">'
                f'{signals["days_on_market"]}d on market</span>'
            )
        if signals['deal_score'] and signals['deal_score'] > 1.0:
            badges.append(
                f'<span style="background:#e67e22;color:#fff;padding:2px 8px;'
                f'border-radius:10px;font-size:11px">'
                f'score {signals["deal_score"]:+.1f}</span>'
            )

        badges_html = " ".join(badges) if badges else (
            '<span style="color:#999;font-size:11px">Best overall value</span>'
        )

        # Rank badge
        rank_color = ['#c0392b', '#e67e22', '#2980b9', '#27ae60', '#8e44ad'][min(i, 4)]
        rank_badge = (
            f'<span style="background:{rank_color};color:#fff;padding:3px 10px;'
            f'border-radius:12px;font-size:13px;font-weight:bold">#{i+1}</span>'
        )

        cards.append(
            f'<div style="background:#f8f9fa;border-left:4px solid {rank_color};'
            f'padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'
            f'{rank_badge}'
            f'<b style="font-size:16px">{price:,.0f} EUR</b>'
            f'<span style="color:#666;font-size:13px">'
            f'{rooms} rooms &middot; {area} m² &middot; floor {floor} &middot; '
            f'{district} &middot; {deal_type}</span>'
            f'<a href="{url}" style="margin-left:auto;font-size:12px">'
            f'View on {source} &rarr;</a></div>'
            f'<div>{badges_html}</div>'
            f'</div>'
        )

    cards_html = "".join(cards)

    return (
        '<div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;'
        'padding:16px;margin:16px 0">'
        '<h3 style="color:#1a5276;border:none;margin:0 0 8px 0">'
        'Top exceptional deals</h3>'
        '<p style="color:#666;font-size:12px;margin:0 0 10px 0">'
        'Best opportunities based on price drops, days on market, '
        'deal score, and price vs area average. Start here.</p>'
        f'{cards_html}'
        '</div>'
    )
