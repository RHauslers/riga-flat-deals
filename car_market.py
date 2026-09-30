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

import config


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


def compute_market_stats(listings, qualified=None):
    """Group the eligible pool by (make, model) and aggregate.

    Returns a list of dicts: make/model (display form from the first
    listing seen), ads, median/min ask (+ url of the cheapest), median
    year, median mileage, and deals = how many of today's qualifying
    deals are this model. Models with fewer than
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
        stats.append({
            "make": make, "model": model, "ads": len(items),
            "median_price": _median(prices),
            "min_price": cheapest.get("price_eur") if cheapest else None,
            "min_url": (cheapest.get("url") or "")
            if cheapest else "",
            "median_year": _median([l.get("year") for l in items]),
            "median_km": _median([l.get("mileage_km") for l in items]),
            "deals": deals,
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


def build_market_html(stats, run_date, total_ads):
    """Render the Market page. website.build() adds the nav on top."""
    if stats is None:
        stats = []
    headers = ["Make", "Model", "Ads", "Median ask", "Cheapest",
               "Median year", "Median km", "Deals today"]
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
        rows.append(
            f"<tr{zebra}>"
            f"<td style='padding:6px' data-sort='{_e(str(s['make']))}'>"
            f"{_e(str(s['make']))}</td>"
            f"<td style='padding:6px' data-sort='{_e(str(s['model']))}'>"
            f"{_e(str(s['model']))}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['ads']}'>{s['ads']}</td>"
            f"<td style='padding:6px;text-align:right' "
            f"data-sort='{s['median_price'] or 0}'>"
            f"{_fmt_eur(s['median_price'])}</td>"
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

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga car market — {_e(str(run_date))}</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:960px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
td,th{{border-bottom:1px solid #eee}}
a{{color:#2874a6;text-decoration:none}}a:hover{{text-decoration:underline}}
.note{{color:#777;font-size:13px}}
.box{{background:#f7f9fb;border:1px solid #dbe4ea;padding:10px 14px;margin:12px 0}}
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
<h1>Riga car market — {_e(str(run_date))}</h1>
<p class="note">Aggregated from today's full eligible pool —
<b>{_fmt_num(total_ads)}</b> ads up to
{_fmt_eur(config.CAR_COMPARABLE_MAX_PRICE_EUR)} across ss.com + pp.lv
after cross-source dedupe. Models with fewer than
{_e(str(config.CAR_MARKET_MIN_LISTINGS))} ads are omitted.
<b>Deals today</b> = ads currently qualifying on the Cars tab.
Click column headers to sort.</p>
<div class="box"><b>How to read this:</b> a model with many ads and a
low median ask is easy to find cheap; <b>Deals today</b> shows where
the actual bargains are right now — sort by it, or by median ask, to
decide which models to watch. <b>Cheapest</b> links to the lowest-priced
ad for that model.</div>
{table}
<hr><p class="note">Generated by Flat_Searcher from the same data as the
Cars tab. Medians are asking prices, not sale prices — always verify on
the source site.</p>
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
    if not data:
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
    return build_market_html(data.get("models"), data.get("date"),
                             data.get("total"))


def save_stats(stats, run_date, total_ads, path=None):
    """Persist stats for website.build() to render."""
    path = path or config.CAR_MARKET_STATS_JSON
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": run_date, "total": total_ads, "models": stats},
                  f, ensure_ascii=False, separators=(",", ":"))
    return path
