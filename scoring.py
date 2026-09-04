# -*- coding: utf-8 -*-
"""
Deal scoring (the 'ML' part).

Two strategies, chosen by data volume:

1. Fallback (history < MIN_TRAIN_ROWS): per-bucket z-score of €/m².
   Bucket = (deal_type, district, rooms). A listing is a great deal when its
   €/m² is far below the bucket mean.

2. Main (history >= MIN_TRAIN_ROWS): multiple linear regression
       price ~ rooms + area + floor + floor_total
                + district(one-hot) + series(one-hot) + source(one-hot)
   Trained on ALL history for that deal_type (retrained every run).
   deal_score = -(residual) / std(training residuals)   (z-score, higher=better)
   A listing with actual price well below the model's prediction = cheap for
   its characteristics = a great deal.

numpy is used for the normal equations. If numpy is unavailable we fall back
to strategy 1 automatically.
"""
import statistics

import config

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fallback: per-bucket €/m² z-score
# ---------------------------------------------------------------------------
def _fallback_scores(new_listings, history):
    # group history €/m² by (deal_type, district, rooms)
    buckets = {}
    for r in history:
        key = (r.get("deal_type"), r.get("district"), _to_int(r.get("rooms")))
        ppu = _to_float(r.get("price_per_m2"))
        if ppu and ppu > 0:
            buckets.setdefault(key, []).append(ppu)

    scored = []
    for l in new_listings:
        key = (l.get("deal_type"), l.get("district"), l.get("rooms"))
        vals = buckets.get(key) or buckets.get((l.get("deal_type"), l.get("district"), None)) or []
        ppu = l.get("price_per_m2") or 0.0
        if len(vals) >= 3:
            mu = statistics.mean(vals)
            sd = statistics.pstdev(vals) or 1.0
            z = (mu - ppu) / sd  # below average -> positive (good deal)
        else:
            # not enough local data: compare against same deal_type overall
            all_ppu = [_to_float(r.get("price_per_m2")) for r in history
                       if r.get("deal_type") == l.get("deal_type")
                       and _to_float(r.get("price_per_m2"))]
            all_ppu = [v for v in all_ppu if v and v > 0]
            if len(all_ppu) >= 3:
                mu = statistics.mean(all_ppu)
                sd = statistics.pstdev(all_ppu) or 1.0
                z = (mu - ppu) / sd
            else:
                z = 0.0
        scored.append((l, z, "zscore"))
    return scored


# ---------------------------------------------------------------------------
# Main: linear regression via numpy normal equations
# ---------------------------------------------------------------------------
def _build_features(l, encoders):
    """Build a numeric feature vector for one listing using fitted encoders."""
    feats = []
    feats.append(_to_float(l.get("rooms")) or 0.0)
    feats.append(_to_float(l.get("area_m2")) or 0.0)
    feats.append(_to_float(l.get("floor_num")) or 0.0)
    feats.append(_to_float(l.get("floor_total")) or 0.0)
    for field, cats in encoders.items():
        val = l.get(field) or ""
        # rare / unseen categories fall into the shared "__other__" bucket
        if val not in cats:
            val = OTHER_CATEGORY
        for c in cats:
            feats.append(1.0 if val == c else 0.0)
    return feats


OTHER_CATEGORY = "__other__"


def _fit_encoders(history_rows):
    """Collect one-hot categories for district/series/source, with cardinality
    caps.

    'series' is ss.com's building series (a small fixed set) but on city24 it is
    the free-text development/project name, whose cardinality grows without
    bound as new developments launch. An unbounded one-hot lets the model
    memorise a development's listings and collapse their residuals to ~0, so
    they can never be flagged as deals. We therefore keep only categories seen
    at least MIN_CATEGORY_COUNT times, cap each field at
    MAX_CATEGORIES_PER_FIELD (most frequent first), and map everything else to
    a shared "__other__" bucket.
    """
    from collections import Counter
    counts = {"district": Counter(), "series": Counter(), "source": Counter()}
    for r in history_rows:
        for f in counts:
            v = r.get(f) or ""
            if v:
                counts[f][v] += 1

    encoders = {}
    for f, ctr in counts.items():
        kept = [v for v, n in ctr.most_common() if n >= config.MIN_CATEGORY_COUNT]
        kept = kept[: config.MAX_CATEGORIES_PER_FIELD]
        # always provide the shared bucket so unseen/rare values have a home
        encoders[f] = sorted(kept) + [OTHER_CATEGORY]
    return encoders


def _regression_scores(new_listings, history_rows):
    encoders = _fit_encoders(history_rows)

    X_rows, y_rows = [], []
    for r in history_rows:
        price = _to_float(r.get("price_eur"))
        area = _to_float(r.get("area_m2"))
        rooms = _to_float(r.get("rooms"))
        if price is None or price <= 0 or area is None or area <= 0 or rooms is None:
            continue
        X_rows.append(_build_features(r, encoders))
        y_rows.append(price)

    if len(X_rows) < config.MIN_TRAIN_ROWS:
        return None  # not enough -> caller falls back

    X = np.array(X_rows, dtype=float)
    y = np.array(y_rows, dtype=float)

    # drop extreme price outliers from training
    mu, sd = y.mean(), y.std()
    if sd > 0:
        keep = np.abs((y - mu) / sd) < config.PRICE_OUTLIER_Z
        X, y = X[keep], y[keep]
    if len(X) < config.MIN_TRAIN_ROWS:
        return None

    # add bias column
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])

    # Ridge regression: (X'X + lambda*I)^-1 X'y, with the intercept unpenalised.
    # Plain lstsq would let a sparse one-hot column (e.g. a single development
    # name) take an extreme coefficient and memorise those rows, shrinking their
    # residuals to ~0 so they could never be detected as bargains.
    n_features = Xb.shape[1]
    penalty = np.eye(n_features) * config.RIDGE_LAMBDA
    penalty[0, 0] = 0.0  # never penalise the intercept
    try:
        coef = np.linalg.solve(Xb.T @ Xb + penalty, Xb.T @ y)
    except np.linalg.LinAlgError:
        coef, *_ = np.linalg.lstsq(Xb, y, rcond=None)

    preds = Xb @ coef
    resid = y - preds
    resid_std = resid.std() or 1.0

    scored = []
    for l in new_listings:
        x = np.array(_build_features(l, encoders), dtype=float)
        xb = np.concatenate([[1.0], x])
        pred = float(xb @ coef)
        actual = _to_float(l.get("price_eur")) or 0.0
        z = -(actual - pred) / resid_std  # cheaper than predicted -> higher z
        scored.append((l, z, "regression"))
    return scored


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_and_rank(new_listings, history):
    """Score new listings and return the top N per deal_type, sorted best first.

    Returns dict: deal_type -> list of (listing, score, method) tuples.
    """
    by_type = {}
    for l in new_listings:
        by_type.setdefault(l.get("deal_type"), []).append(l)

    result = {}
    for dt, items in by_type.items():
        hist_dt = [r for r in history if r.get("deal_type") == dt]

        scored = None
        if _HAS_NUMPY:
            try:
                scored = _regression_scores(items, hist_dt)
            except Exception as e:
                print(f"[scoring] regression failed for {dt}: {e}")
                scored = None
        if scored is None:
            scored = _fallback_scores(items, hist_dt)

        scored.sort(key=lambda t: t[1], reverse=True)
        result[dt] = scored[: config.TOP_N_PER_TYPE]
    return result


def model_status(history):
    """Human-readable note about which scoring method will be used."""
    counts = {}
    for r in history:
        counts[r.get("deal_type")] = counts.get(r.get("deal_type"), 0) + 1
    lines = []
    for dt in config.DEAL_TYPES:
        n = counts.get(dt, 0)
        method = "linear regression" if (n >= config.MIN_TRAIN_ROWS and _HAS_NUMPY) else "z-score fallback"
        lines.append(f"{dt}: {n} history rows -> {method}")
    return "; ".join(lines)
