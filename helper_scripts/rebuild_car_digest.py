# -*- coding: utf-8 -*-
"""Re-render an existing car digest through the CURRENT car_digest code —
no rescraping.

Every data/digests/cars_YYYY-MM-DD.html embeds that day's deduplicated
eligible market as <script id="car-market-data"> JSON — the exact input
rows the page was built from (annotations like _first_seen, _price_hist,
_relisted and inspection included). This script decodes that embed,
re-runs car_value.score_and_rank on it (deterministic — the same
qualified/assessed sets come back), recovers the row badges, coverage
counts and the gone-table rows the ORIGINAL page rendered (mapped back
to snapshot recent_gone dicts by url/spec — a same-date filter alone
overcounts on days the scan ran twice), then calls
car_digest.build_html() again and overwrites the digest in place.
State files (car_seen.json, car_market_snapshot.json,
car_market_history.json) are read-only inputs — nothing rescrapes or
rewrites them.

Use after changing car_digest.py / web_style.py so today's Cars tab
shows the new rendering without waiting for the next daily run.
website.build() is invoked at the end to refresh docs/cars.html and
docs/archive/cars_<date>.html. Badges are parsed from the original page
because the run mutated car_seen.json afterwards (recomputing them now
would turn PRICE DROP into STILL ACTIVE).

Run:  python -X utf8 helper_scripts/rebuild_car_digest.py
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import car_digest
import car_market
import car_value
import cars
import config
import utils
import website

# ---------------------------------------------------------------------------
# Settings (hard-coded — adjust here, not via CLI args)
# ---------------------------------------------------------------------------
REBUILD_DATE = None          # 'YYYY-MM-DD' or None = newest cars_*.html
EMBED_RE = re.compile(
    r'<script type="application/json" id="car-market-data">(.*?)</script>',
    re.S)
FILE_DATE_RE = re.compile(r"cars_(\d{4}-\d{2}-\d{2})\.html$")
ROW_RE = re.compile(r"<tr[^>]*id='r-([^']+)'[^>]*>(.*?)</tr>", re.S)
STATUS_BADGE_RE = re.compile(
    r"class='badge (?:b-new|b-down|b-ended|b-reg)'>([^<]+)</span>")
COVERAGE_RE = re.compile(r"(ss\.com|pp\.lv): (\d+) scraped, (\d+) eligible")
DROP_BLOCK_RE = re.compile(r"Dropped as ineligible: ([^<]+)</p>")
DROP_PAIR_RE = re.compile(r"([^,×]+?) ×(\d+)")
ERR_RE = re.compile(r"<li><b>([^<]+)</b>: ([^<]+)</li>")
GONE_TABLE_RE = re.compile(r"id='car-gone'>(.*?)</table>", re.S)
GONE_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
GONE_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
GONE_URL_RE = re.compile(r'href="([^"]+)"[^>]*>view</a>')
GONE_PRICE_RE = re.compile(r"data-sort='([\d.]+)'")
TAG_RE = re.compile(r"<[^>]+>")
_ADS_IDX = 2


def _pick_digest():
    """(run_date, path) of the digest to rebuild."""
    files = sorted(f for f in glob.glob(
        os.path.join(config.DIGEST_DIR, "cars_*.html"))
        if FILE_DATE_RE.search(f))
    if not files:
        return None, None
    if REBUILD_DATE:
        for f in files:
            if FILE_DATE_RE.search(f).group(1) == REBUILD_DATE:
                return REBUILD_DATE, f
        return None, None
    f = files[-1]
    return FILE_DATE_RE.search(f).group(1), f


def _parse_badges(html):
    """{listing_key: badge_text} from the rows the page rendered."""
    badges = {}
    for m in ROW_RE.finditer(html):
        key, body = m.group(1), m.group(2)
        b = STATUS_BADGE_RE.search(body)
        if b:
            badges[key] = b.group(1)
    return badges


def _parse_coverage(html):
    """source_counts from the 'N scraped, N eligible' coverage line."""
    counts = {name: {"raw": int(raw), "eligible": int(eli)}
              for name, raw, eli in COVERAGE_RE.findall(html)}
    drop = DROP_BLOCK_RE.search(html)
    if drop:
        counts["_drop_reasons"] = {
            reason.strip(): int(n)
            for reason, n in DROP_PAIR_RE.findall(drop.group(1))}
    return counts


def _parse_errors(html):
    """The outage warn box's <li><b>src</b>: msg</li> pairs, if any."""
    return {src: msg for src, msg in ERR_RE.findall(html)}


def _market_pulse(listings):
    pool_med = utils.median(
        [utils.to_float(l.get("price_eur")) for l in listings
         if l.get("price_eur")])
    if not pool_med:
        return None
    hist = car_market.load_history().get("_all", [])
    ads_prev = (hist[-2][_ADS_IDX]
                if len(hist) > 1 and len(hist[-2]) > _ADS_IDX else None)
    return {"median": pool_med,
            "delta": utils.delta_7d(hist),
            "ads": len(listings),
            "ads_delta": (len(listings) - ads_prev
                          if ads_prev is not None else None)}


def _parse_gone_table(html):
    """[{u, mk, mo, y, p}] per rendered gone-table row, in table order.
    The gbadge letter (M/A/?) prepended by newer renders is stripped —
    makes are stored lowercase so a leading uppercase marker can only be
    the badge."""
    sec = GONE_TABLE_RE.search(html)
    if not sec:
        return []
    out = []
    for m in GONE_ROW_RE.finditer(sec.group(1)):
        cells = GONE_CELL_RE.findall(m.group(1))
        if len(cells) < 4:
            continue
        url_m = GONE_URL_RE.search(cells[-1])
        txt = TAG_RE.sub("", cells[0]).strip()
        if txt and txt[0] in "MA?":
            txt = txt[1:].strip()
        parts = txt.split()
        y = parts.pop() if parts and parts[-1].isdigit() else ""
        mk, _, mo = " ".join(parts).partition(" ")
        pm = GONE_PRICE_RE.search(cells[1])
        out.append({"u": url_m.group(1) if url_m else "", "mk": mk,
                    "mo": mo, "y": y,
                    "p": float(pm.group(1)) if pm else None})
    return out


def _gone_rows(run_date, html):
    """The gone list the ORIGINAL page rendered, mapped back to snapshot
    recent_gone dicts (url first, spec+price fallback) so the 'Tracked'
    lookup still resolves. Filtering recent_gone by date alone is wrong
    on days the scan ran twice — both runs stamp the same date."""
    parsed = _parse_gone_table(html)
    snap = cars.load_snapshot()
    cand = [r for r in snap.get("recent_gone") or []
            if r.get("gone") == run_date]
    out, used = [], set()
    for p in parsed:
        hit = None
        for i, c in enumerate(cand):
            if i in used:
                continue
            if p["u"] and c.get("u") == p["u"]:
                hit = i
                break
            if not p["u"] and p["mk"] and \
                    (c.get("mk"), c.get("mo"), str(c.get("y"))) == \
                    (p["mk"], p["mo"], p["y"]) and \
                    (p["p"] is None or
                     abs((utils.to_float(c.get("p")) or 0) - p["p"]) < 0.5):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            out.append(cand[hit])
        else:
            out.append({"k": "", "p": p["p"], "mk": p["mk"], "mo": p["mo"],
                        "y": p["y"], "g": "", "u": p["u"]})
    return out


def main():
    run_date, path = _pick_digest()
    if not path:
        print(f"[rebuild] no cars_*.html under {config.DIGEST_DIR} "
              f"(REBUILD_DATE={REBUILD_DATE})")
        return
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    em = EMBED_RE.search(html)
    if not em:
        print(f"[rebuild] {run_date}: no car-market-data embed "
              "(error-page digest?) — nothing to rebuild")
        return
    payload = json.loads(em.group(1))
    listings = utils.dict_decode(payload)
    badges = _parse_badges(html)
    source_counts = _parse_coverage(html)
    source_errors = _parse_errors(html)
    qualified, assessed = car_value.score_and_rank(listings)
    gone = _gone_rows(run_date, html)
    html_new = car_digest.build_html(
        qualified, assessed, source_counts, source_errors, badges,
        run_date, market=listings, gone=gone,
        seen=cars._read_seen(), market_pulse=_market_pulse(listings))
    utils.write_text(path, html_new)
    print(f"[rebuild] {run_date}: {len(listings)} market rows -> "
          f"{len(qualified)} qualifying, {len(badges)} badges, "
          f"{len(gone)} gone, {os.path.getsize(path) // 1024} KB -> {path}")
    website.build()


if __name__ == "__main__":
    main()
