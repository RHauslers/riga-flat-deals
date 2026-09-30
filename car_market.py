"""Per-model market stats for the Market tab (docs/market.html).

The Cars tab answers "which specific ads are bargains"; this page answers
"which models are worth looking at" — how many ads exist per model, the
typical asking price, the cheapest example on sale, and how many of
today's qualifying deals belong to each model. Pure aggregate math over
the same deduplicated eligible pool the digest scores — no extra scraping.

Flow: cars.run() computes stats and writes config.CAR_MARKET_STATS_JSON;
website.build() renders docs/market.html from it and injects the nav.
"""

import json
import os
import statistics
from html import escape as _e
from urllib.parse import quote as _q

import config
import flat_market


def _fmt_eur(v):
    if v is None:
        return "—"
    try:
        return "€{:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v):
    if v is None:
        return "—"
    try:
        return "{:,}".format(int(round(float(v))))
    except (TypeError, ValueError):
        return "—"


def _median(values):
    vals = [float(v) for v in values if v is not None]
    try:
        return statistics.median(vals) if vals else None
    except (TypeError, ValueError):
        return None


def _group_key(listing):
    return (str(listing.get("make") or "").strip().lower(),
            str(listing.get("model") or "").strip().lower())


def compute_market_stats(listings, qualified=None, today=None):
    """Group the eligible pool by (make, model) and aggregate.

    Returns a list of dicts: make/model (display form from the first
    listing seen), ads, median/min ask (+ url of the cheapest), median
    year, median mileage, deals = how many of today's qualifying deals
    are this model, and new_today = ads first seen on ``today`` (the
    _first_seen annotation set by cars.run()). Models with fewer than
    config.CAR_MARKET_MIN_LISTINGS ads are dropped as noise.
    """
    deal_keys = {f"{l.get('source')}:{l.get('id')}" for l in (qualified or [])}
    groups = {}
    display = {}
    for l in listings:
        key = _group_key(l)
        if not all(key):
            continue
        groups.setdefault(key, []).append(l)
        display.setdefault(key, (l.get("make"), l.get("model")))

    stats = []
    for key, items in groups.items():
        if len(items) < config.CAR_MARKET_MIN_LISTINGS:
            continue
        make, model = display[key]
        prices = [l.get("price_eur") for l in items if l.get("price_eur")]
        cheapest = min((l for l in items if l.get("price_eur")),
                       key=lambda l: l["price_eur"], default=None)
        deals = sum(1 for l in items
                    if f"{l.get('source')}:{l.get('id')}" in deal_keys)
        new_today = (sum(1 for l in items if l.get("_first_seen") == today)
                     if today else 0)
        stats.append({
            "make": make, "model": model, "ads": len(items),
            "median_price": _median(prices),
            "min_price": cheapest.get("price_eur") if cheapest else None,
            "min_url": (cheapest.get("url") or "")
            if cheapest else "",
            "median_year": _median([l.get("year") for l in items]),
            "median_km": _median([l.get("mileage_km") for l in items]),
            "deals": deals, "new_today": new_today,
        })
    stats.sort(key=lambda s: (-s["deals"], -s["ads"],
                              s["median_price"] or 0))
    return stats


_SORT_JS = """
// Click-to-sort (same mechanism as the deals digests): first click sorts
// ascending, second click reverses; numeric columns use data-sort.
var sortState = {};
function sortTable(tableId, colIdx) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var ths = table.querySelectorAll('th.sort-th');
  ths.forEach(function(th) { th.classList.remove('sort-asc','sort-desc'); });
  var rows = Array.from(table.querySelectorAll('tr')).slice(1);
  var key = tableId + '_' + colIdx;
  sortState[key] = !sortState[key];
  var asc = sortState[key];
  var clickedTh = table.querySelectorAll('th')[colIdx];
  if (clickedTh) clickedTh.classList.add(asc ? 'sort-asc' : 'sort-desc');
  rows.sort(function(a, b) {
    var va = a.children[colIdx].getAttribute('data-sort');
    var vb = b.children[colIdx].getAttribute('data-sort');
    if (va === null || vb === null) return 0;
    va = va.trim(); vb = vb.trim();
    var na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) {
      return asc ? na - nb : nb - na;
    }
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(function(r) { table.appendChild(r); });
}
"""


def _spark_html(points):
    """Inline SVG sparkline for a [[date, price], ...] trail; returns
    (html, pct_change) — empty html when fewer than 2 points exist.
    Same color language as the listing trails: down = red, up = green."""
    vals = [p[1] for p in points if len(p) == 2 and p[1] is not None]
    if len(vals) < 2 or not vals[0]:
        return "", 0
    pct = (vals[-1] - vals[0]) / vals[0] * 100
    w, h = 64, 16
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    coords = " ".join(
        f"{i * (w - 4) / (len(vals) - 1) + 2:.1f},"
        f"{h - 3 - (v - lo) / span * (h - 6):.1f}"
        for i, v in enumerate(vals))
    color = ("#c0392b" if vals[-1] < vals[0]
             else "#27ae60" if vals[-1] > vals[0] else "#7f8c8d")
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
            f"style='vertical-align:-3px' "
            f"title='{pct:+.1f}% since {points[0][0]}'>"
            f"<polyline points='{coords}' fill='none' "
            f"stroke='{color}' stroke-width='1.5'/></svg>", pct)


def build_market_html(stats, run_date, total_ads, history=None,
                      flat_stats=None):
    """Render the Market page. website.build() adds the nav on top."""
    if stats is None:
        stats = []
    history = history or {}
    headers = ["Make", "Model", "Ads", "New", "Median ask", "Trend",
               "Cheapest", "Median year", "Median km", "Deals today"]
    head_cells = "".join(
        "<th class='sort-th' style='padding:6px;{align}' "
        "onclick=\"sortTable('car-market', {i})\">{name}</th>".format(
            i=i, name=_e(name),
            align="text-align:left" if i < 2 else "text-align:right")
        for i, name in enumerate(headers))

    rows = []
    for i, s in enumerate(stats):
        zebra = " style='background:#fafafa'" if i % 2 else ""
        cheap = (f"<a href='{_e(s['min_url'])}' target='_blank' "
                 f"rel='noopener noreferrer'>{_fmt_eur(s['min_price'])}"
                 f"</a>" if s.get("min_url") else _fmt_eur(s["min_price"]))
        deals = (f"<b style='color:#1a7a3a'>{s['deals']}</b>"
                 if s["deals"] else str(s["deals"]))
        year = s["median_year"]
        make_l = (f"<a href='cars.html?make={_q(str(s['make']))}'>"
                  f"{_e(str(s['make']))}</a>")
        model_l = (f"<a href='cars.html?model={_q(str(s['model']))}'>"
                   f"{_e(str(s['model']))}</a>")
        spark, pct = _spark_html(
            history.get(f"{_group_key(s)[0]}|{_group_key(s)[1]}", []))
        rows.append(
            f"<tr{zebra}>"
            f"<td style='padding:6px' data-sort='{_e(str(s['make']))}'>"
            f"{make_l}</td>"
            f"<td style='padding:6px' data-sort='{_e(str(s['model']))}'>"
            f"{model_l}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['ads']}'>{s['ads']}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s.get('new_today') or 0}'>"
            f"{s.get('new_today') or 0}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['median_price'] or 0}'>"
            f"{_fmt_eur(s['median_price'])}</td>"
            f"<td style='padding:6px' data-sort='{pct:.1f}'>"
            f"{spark}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['min_price'] or 0}'>{cheap}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{year or 0}'>"
            f"{int(year) if year else '—'}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['median_km'] or 0}'>"
            f"{_fmt_num(s['median_km'])}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['deals']}'>{deals}</td>"
            f"</tr>")

    table = (
        f"<table id='car-market'><tr>{head_cells}</tr>{''.join(rows)}"
        "</table>" if rows else
        "<p>No models with enough ads to summarise yet.</p>")

    flat_section = flat_market.flat_section_html(
        flat_stats.get("districts"), flat_stats.get("date")) \
        if flat_stats else ("<p class='note'>No flat stats yet — they are "
                            "written by the daily flat scan.</p>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga market — {_e(str(run_date))}</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:960px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}
h2{{color:#1a5276;border-bottom:2px solid #2874a6;padding-bottom:4px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
td,th{{border-bottom:1px solid #eee}}
a{{color:#2874a6;text-decoration:none}}a:hover{{text-decoration:underline}}
.note{{color:#777;font-size:13px}}
.box{{background:#f7f9fb;border:1px solid #dbe4ea;padding:10px 14px;margin:12px 0}}
.mtabs{{margin:4px 0 16px}}
.mtab{{padding:6px 18px;border:1px solid #2874a6;background:#fff;color:#2874a6;
border-radius:4px;cursor:pointer;font-weight:bold;font-size:14px}}
.mtab+.mtab{{margin-left:6px}}
.mtab-on{{background:#2874a6;color:#fff}}
th{{user-select:none;cursor:default;position:relative}}
th.sort-th{{cursor:pointer}}
th.sort-th:hover{{background:#e8e8e8}}
th.sort-th::after{{content:"\\21C5";font-size:10px;color:#bbb;margin-left:4px;opacity:0}}
th.sort-th:hover::after{{opacity:1}}
th.sort-asc::after{{content:"\\2191";font-size:10px;color:#1a5276;margin-left:4px;opacity:1}}
th.sort-desc::after{{content:"\\2193";font-size:10px;color:#1a5276;margin-left:4px;opacity:1}}
th{{position:sticky;top:0;background:#f0f0f0;z-index:1}}
tr:hover td{{background:#f6f9fc}}
</style>
<script>{_SORT_JS}</script>
</head><body>
<h1>Riga market — {_e(str(run_date))}</h1>
<div class="mtabs">
<button type="button" class="mtab" id="mtab-cars"
 onclick="__mktTab('cars')">Cars</button>
<button type="button" class="mtab" id="mtab-flats"
 onclick="__mktTab('flats')">Flats</button>
</div>
<div id="mpane-cars">
<h2>Cars — by make &amp; model</h2>
<p class="note">Aggregated from today's full eligible pool —
<b>{_fmt_num(total_ads)}</b> ads across ss.com + pp.lv
after cross-source dedupe. Models with fewer than
{_e(str(config.CAR_MARKET_MIN_LISTINGS))} ads are omitted.
<b>Deals today</b> = ads currently qualifying on the Cars tab;
<b>New</b> = ads our scan saw for the first time today.
Click column headers to sort.</p>
<div class="box"><b>How to read this:</b> a model with many ads and a
low median ask is easy to find cheap; <b>Deals today</b> shows where
the actual bargains are right now — sort by it, or by median ask, to
decide which models to watch. <b>Cheapest</b> links to the lowest-priced
ad for that model; clicking a <b>make or model name</b> opens the Cars
tab already filtered to it.</div>
{table}
</div>
<div id="mpane-flats" style="display:none">
{flat_section}
</div>
<hr><p class="note">Generated by Flat_Searcher from the same data as the
Cars and Flats tabs. Medians are asking prices, not sale prices —
always verify on the source site.</p>
<script>
function __mktTab(w) {{
  ['cars', 'flats'].forEach(function (p) {{
    var on = (p === w);
    var pane = document.getElementById('mpane-' + p);
    var btn = document.getElementById('mtab-' + p);
    if (pane) pane.style.display = on ? '' : 'none';
    if (btn) btn.classList.toggle('mtab-on', on);
  }});
}}
(function () {{
  var qs = (typeof location !== 'undefined' && location.search) || '';
  var hm = (typeof location !== 'undefined' && location.hash) || '';
  var qp = '';
  try {{ qp = new URLSearchParams(qs).get('m') || ''; }} catch (e) {{}}
  __mktTab((qp === 'flats' || hm === '#flats') ? 'flats' : 'cars');
}})();
</script>
</body></html>"""


def load_stats(path=None):
    """Read the stats JSON written by cars.run(); None when absent."""
    path = path or config.CAR_MARKET_STATS_JSON
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_page(path=None):
    """Render docs/market.html content from the stats file (or a
    placeholder when no stats exist yet)."""
    data = load_stats(path)
    flat_data = flat_market.load_stats()
    if not data and not flat_data:
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga car market</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:800px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}a{{color:#2874a6}}
</style></head><body>
<h1>Riga car market</h1>
<p>Not generated yet — the market stats are written by the daily car
scan.</p>
</body></html>"""
    return build_market_html(data.get("models") if data else [],
                             data.get("date") if data else "?",
                             data.get("total") if data else 0,
                             load_history(), flat_data)


def save_stats(stats, run_date, total_ads, path=None):
    """Persist stats for website.build() to render. Also appends today's
    per-model median ask to the history file so the page can draw a trend
    sparkline once a model has been tracked on multiple days."""
    path = path or config.CAR_MARKET_STATS_JSON
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": run_date, "total": total_ads, "models": stats},
                  f, ensure_ascii=False, separators=(",", ":"))
    _append_history(stats, run_date)
    return path


def _append_history(stats, run_date,
                    path=None):
    """car_market_history.json: {make|model: [[date, median_eur], ...]},
    capped at CAR_MARKET_HISTORY_MAX_POINTS points per model."""
    path = path or config.CAR_MARKET_HISTORY_JSON
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = {}
    for s in stats:
        if s.get("median_price") is None:
            continue
        key = f"{_group_key(s)[0]}|{_group_key(s)[1]}"
        pts = hist.setdefault(key, [])
        if pts and pts[-1][0] == run_date:
            pts[-1][1] = s["median_price"]      # same-day re-run updates
        else:
            pts.append([run_date, s["median_price"]])
        del pts[:-config.CAR_MARKET_HISTORY_MAX_POINTS]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass


def load_history(path=None):
    """{make|model: [[date, median_eur], ...]} — {} when absent."""
    path = path or config.CAR_MARKET_HISTORY_JSON
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
