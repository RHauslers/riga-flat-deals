# -*- coding: utf-8 -*-
"""
Classify today's top deals into badges and split into sections.

Inputs:
  - scored_by_type: dict {deal_type: [(listing, score, method), ...]} (top N)
  - seen_deals:    dict {key: {first_shown_date, last_shown_date,
                               last_shown_price, last_shown_score, deal_type}}
  - last_digest:   dict {date, rent: [...], sale: [...]} from yesterday

Outputs:
  - main_deals:    dict {deal_type: [(listing, score, method, badge, badge_detail)]}
                  (NEW / PRICE_DROP / REAPPEARED -> shown in the main table)
  - still_active:  dict {deal_type: [(listing, score, method)]}
                  (STILL_ACTIVE -> shown in a separate greyed section)

Badges (priority order, first match wins):
  1. NEW         - key not in seen_deals at all
  2. PRICE_DROP  - key in seen_deals AND today price dropped >= PRICE_DROP_MIN_PCT
  3. STILL_ACTIVE- key was in yesterday's top-N (last_digest) -> separate section
  4. REAPPEARED  - key in seen_deals but not in yesterday's top-N -> main table
"""
from datetime import date, datetime

import config


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _days_since(date_str, today=None):
    """Return days between today and date_str (ISO), or None if unparseable."""
    if not date_str or date_str == "unknown":
        return None
    try:
        d = datetime.fromisoformat(date_str).date()
    except (ValueError, TypeError):
        return None
    today = today or date.today()
    return (today - d).days


def _pct_drop(old_price, new_price):
    if not old_price or old_price <= 0 or new_price is None:
        return 0.0
    return ((old_price - new_price) / old_price) * 100.0


def _last_digest_keys(last_digest, deal_type):
    """Return set of keys that were in yesterday's top-N for this deal type."""
    if not last_digest or not isinstance(last_digest, dict):
        return set()
    items = last_digest.get(deal_type, [])
    return {it.get("key") for it in items if it.get("key")}


def _key(listing):
    return f"{listing.get('source')}:{listing.get('id')}"


def classify(scored_by_type, seen_deals, last_digest):
    """Return (main_deals, still_active) dicts.

    Each main_deals entry: (listing, score, method, badge, badge_detail)
    Each still_active entry: (listing, score, method)
    """
    today = date.today()
    main_deals = {}
    still_active = {}

    seen_deals = seen_deals or {}
    last_digest = last_digest or {}

    for dt, items in scored_by_type.items():
        yest_keys = _last_digest_keys(last_digest, dt)
        main_list = []
        still_list = []

        for entry in items:
            listing, score, method = entry
            key = _key(listing)
            today_price = _to_float(listing.get("price_eur"))
            is_daily = listing.get("price_unit") == "day"

            if key not in seen_deals:
                # brand new
                badge = "SHORT_TERM" if is_daily else "NEW"
                main_list.append((listing, score, method, badge, None))
                continue

            prev = seen_deals[key] or {}
            prev_price = _to_float(prev.get("last_shown_price"))
            drop_pct = _pct_drop(prev_price, today_price)

            if prev_price and drop_pct >= config.PRICE_DROP_MIN_PCT:
                # price dropped meaningfully since last shown
                detail = (f"was {int(prev_price)} EUR "
                          f"(\u2193{drop_pct:.0f}%)")
                badge = "SHORT_TERM" if is_daily else "PRICE_DROP"
                main_list.append((listing, score, method, badge, detail))
                continue

            if key in yest_keys:
                # was in yesterday's top N -> still active section
                # (unless too stale)
                days = _days_since(prev.get("last_shown_date"), today)
                if days is not None and days > config.STILL_ACTIVE_MAX_DAYS:
                    badge = "SHORT_TERM" if is_daily else "REAPPEARED"
                    main_list.append((listing, score, method, badge, None))
                else:
                    still_list.append((listing, score, method))
                continue

            # seen before but not in yesterday's top N -> reappeared
            badge = "SHORT_TERM" if is_daily else "REAPPEARED"
            main_list.append((listing, score, method, badge, None))

        main_deals[dt] = main_list
        still_active[dt] = still_list

    return main_deals, still_active


# ---------------------------------------------------------------------------
# "vs yesterday" comparison header
# ---------------------------------------------------------------------------
def comparison_header(scored_by_type, last_digest):
    """Return an HTML string summarising today vs yesterday per deal type."""
    last_digest = last_digest or {}
    last_date = last_digest.get("date")

    if not last_date:
        return ("<div style='background:#eaf6ff;padding:10px;border-radius:6px;margin:12px 0'>"
                "<strong>First run</strong> — establishing baseline for daily comparisons. "
                "Tomorrow you'll see how today's best deals compare.</div>")

    lines = [f"<div style='background:#eaf6ff;padding:10px;border-radius:6px;margin:12px 0'>"
             f"<strong>Compared to {last_date}:</strong><br>"]

    any_change = False
    for dt in config.DEAL_TYPES:
        today_items = scored_by_type.get(dt, [])
        yest_items = last_digest.get(dt, [])

        if today_items:
            today_best = max((s for _, s, _ in today_items), default=None)
        else:
            today_best = None

        if yest_items:
            yest_best = max((it.get("score") for it in yest_items), default=None)
        else:
            yest_best = None

        new_count = sum(1 for entry in today_items
                        if _key(entry[0]) not in {it.get("key") for it in yest_items})

        if today_best is not None and yest_best is not None:
            if today_best > yest_best:
                lines.append(
                    f"&bull; <strong>{dt.upper()}</strong>: today's best deal "
                    f"(score {today_best:+.2f}) <strong>beats</strong> "
                    f"yesterday's best ({yest_best:+.2f}). {new_count} new deal(s).")
                any_change = True
            elif today_best < yest_best:
                lines.append(
                    f"&bull; <strong>{dt.upper()}</strong>: today's best deal "
                    f"(score {today_best:+.2f}) falls short of yesterday's "
                    f"({yest_best:+.2f}). {new_count} new deal(s).")
            else:
                lines.append(
                    f"&bull; <strong>{dt.upper()}</strong>: today's best deal "
                    f"(score {today_best:+.2f}) ties yesterday's best. "
                    f"{new_count} new deal(s).")
        elif today_best is not None:
            lines.append(
                f"&bull; <strong>{dt.upper()}</strong>: today's best deal "
                f"(score {today_best:+.2f}). No prior baseline. "
                f"{new_count} new deal(s).")
        else:
            lines.append(
                f"&bull; <strong>{dt.upper()}</strong>: no deals today.")

    lines.append("</div>")
    return "".join(lines)
