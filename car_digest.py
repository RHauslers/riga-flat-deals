# -*- coding: utf-8 -*-
"""
Car digest HTML builder — website only (no car email).

build_html(qualified, assessed, source_counts, source_errors, badges,
run_date) -> str
"""
import html
import json
from datetime import date, datetime
from urllib.parse import urlparse

import config

ALLOWED_HOSTS = {"www.ss.com", "ss.com", "pp.lv", "www.pp.lv"}

BADGE_STYLES = {
    "NEW": "background:#27ae60;color:#fff",
    "PRICE DROP": "background:#c0392b;color:#fff",
    "STILL ACTIVE": "background:#7f8c8d;color:#fff",
    "REAPPEARED": "background:#e67e22;color:#fff",
}


def _riga_stamp(run_date):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Riga")).strftime("%Y-%m-%d %H:%M") + " Riga time"
    except Exception:
        return str(run_date)


def _safe_url(url):
    """Return the URL only if it is an https link to an allow-listed host."""
    try:
        p = urlparse(str(url or ""))
    except Exception:
        return None
    if p.scheme == "https" and p.netloc.lower() in ALLOWED_HOSTS:
        return str(url)
    return None


def _link(url, text):
    u = _safe_url(url)
    label = html.escape(str(text))
    if not u:
        return label
    return f'<a href="{html.escape(u, quote=True)}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _e(value):
    return html.escape("" if value is None else str(value))


def _fmt(value, suffix=""):
    return f"{_e(value)}{suffix}" if value is not None else "—"


def _fmt_eur(value):
    if value is None:
        return "—"
    try:
        return f"€{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return "—"


def _spec_text(l):
    parts = [
        str(l.get("year") or "?"),
        l.get("fuel") or "?",
        f"{l.get('engine_l')}L" if l.get("engine_l") else "?L",
        f"{int(l['mileage_km'] / 1000)}k km" if isinstance(l.get("mileage_km"), (int, float)) else "? km",
    ]
    extras = [x for x in (l.get("gearbox"), l.get("body"), l.get("location")) if x]
    return _e(" · ".join(parts + [str(x) for x in extras]))


def _badge_html(key, badges):
    b = badges.get(key)
    if not b:
        return ""
    style = BADGE_STYLES.get(b, "background:#555;color:#fff")
    return (f'<span style="{style};padding:2px 6px;border-radius:3px;'
            f'font-size:11px;font-weight:bold">{_e(b)}</span>')


def _listing_links(l):
    key_html = _link(l.get("url"), l.get("source") or "ad")
    for extra in l.get("also_on") or []:
        key_html += " + " + _link(extra.get("url"), extra.get("source") or "ad")
    return key_html


def _pool_note(l):
    """Small grey line under the median ask showing the comparable pool's
    own median year/mileage, so the score's condition adjustments (low
    mileage / newer year vs peers) are visible to the reader."""
    year, mileage = l.get("_pool_year"), l.get("_pool_mileage")
    if year is None or mileage is None:
        return ""
    try:
        km = int(round(float(mileage) / 1000))
    except (TypeError, ValueError):
        return ""
    return (f"<br><span style='color:#777;font-size:11px'>"
            f"pool ~{_e(year)} · ~{km}k km</span>")


# Fields embedded per listing so the browser can recompute deals for a
# custom budget (car_value.score_and_rank ported to JS). Order matters:
# the embedded JSON stores rows as arrays in this order.
_MARKET_FIELDS = ("source", "id", "make", "model", "year", "mileage_km",
                  "fuel", "engine_l", "gearbox", "body", "price_eur", "url")


def _market_data_html(market):
    """Embed the day's deduplicated eligible market (all prices up to the
    comparable ceiling, not just today's candidates) as JSON, with the
    scoring config, so the page can re-rank for any budget in the browser.
    URLs are allow-listed here — the same rule _link() applies to rows."""
    rows = []
    for l in market or []:
        url = l.get("url")
        if not _safe_url(url):
            url = ""
        rows.append([l.get(f) for f in _MARKET_FIELDS[:-1]] + [url])
    cfg = {
        "minPrice": config.CAR_MIN_PRICE_EUR,
        "compMax": config.CAR_COMPARABLE_MAX_PRICE_EUR,
        "defaultMax": config.CAR_PRICE_CEILING_EUR,
        "minYear": config.CAR_MIN_YEAR,
        "maxMileage": config.CAR_MAX_MILEAGE_KM,
        "highMileageWarn": config.CAR_HIGH_MILEAGE_WARNING_KM,
        "ageWarnYears": config.CAR_AGE_WARNING_YEARS,
        "yearTol": config.CAR_YEAR_TOLERANCE,
        "mileageTol": config.CAR_MILEAGE_TOLERANCE_KM,
        "engineTol": config.CAR_ENGINE_TOLERANCE_L,
        "minComps": config.CAR_MIN_COMPARABLES,
        "goodDiscount": config.CAR_GOOD_MIN_DISCOUNT_PCT,
        "goodSavings": config.CAR_GOOD_MIN_SAVINGS_EUR,
        "scoreCenter": config.CAR_SCORE_CENTER,
        "discountMult": config.CAR_SCORE_DISCOUNT_MULTIPLIER,
        "mileagePoints": config.CAR_SCORE_MILEAGE_POINTS,
        "yearPoints": config.CAR_SCORE_YEAR_POINTS,
        "scoreMax": config.CAR_SCORE_MAX,
        "fuels": ["petrol", "diesel", "hybrid", "electric", "lpg"],
    }
    payload = {"config": cfg, "fields": list(_MARKET_FIELDS), "rows": rows}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("</", "<\\/")  # keep JSON out of </script> parsing
    return ('<script type="application/json" id="car-market-data">'
            + text + "</script>")


# In-browser recomputation for a custom budget. Mirrors
# car_value.score_and_rank (eligibility, comparable grouping on
# make/model/fuel, tolerance matching, pool medians, discount gate,
# condition-adjusted score). Constants come from the embedded config so
# Python stays the single source of truth. Known divergence: Math.round
# rounds halves up while Python's round() is banker's rounding — scores
# can differ by 1 on exact .5 cases. Exposes window.__carBudget for the
# Node parity test (tests/test_car_search.py).
CAR_BUDGET_JS = """
(function () {
  var dataEl = document.getElementById('car-market-data');
  var input = document.getElementById('car-budget-input');
  var statusEl = document.getElementById('car-budget-status');
  var defaultView = document.getElementById('car-default-view');
  var customView = document.getElementById('car-custom-view');
  if (!dataEl || !input || !defaultView || !customView) return;
  var payload = JSON.parse(dataEl.textContent);
  var cfg = payload.config, F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  var thisYear = new Date().getFullYear();

  function median(arr) {
    var s = arr.slice().sort(function (a, b) { return a - b; });
    var n = s.length;
    if (!n) return 0;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }

  // eligible(): the market band [minPrice, compMax] with usable specs
  function eligible(r) {
    var p = r[idx.price_eur], y = r[idx.year], m = r[idx.mileage_km];
    if (p == null || p < cfg.minPrice || p > cfg.compMax) return false;
    if (y == null || y < cfg.minYear) return false;
    if (m == null || m < 0 || m > cfg.maxMileage) return false;
    if (!r[idx.make] || !r[idx.model]) return false;
    if (cfg.fuels.indexOf(r[idx.fuel]) < 0) return false;
    if (r[idx.fuel] !== 'electric' && !(r[idx.engine_l] > 0)) return false;
    return true;
  }

  var market = payload.rows.filter(eligible);
  var groups = {};
  market.forEach(function (r) {
    var k = r[idx.make] + '|' + r[idx.model] + '|' + r[idx.fuel];
    (groups[k] = groups[k] || []).push(r);
  });

  function comparable(a, b) {
    if (a[idx.source] === b[idx.source] && a[idx.id] === b[idx.id]) return false;
    if (Math.abs(a[idx.year] - b[idx.year]) > cfg.yearTol) return false;
    if (Math.abs(a[idx.mileage_km] - b[idx.mileage_km]) > cfg.mileageTol) return false;
    if (a[idx.gearbox] && b[idx.gearbox] && a[idx.gearbox] !== b[idx.gearbox]) return false;
    if (a[idx.body] && b[idx.body] && a[idx.body] !== b[idx.body]) return false;
    var ea = a[idx.engine_l], eb = b[idx.engine_l];
    if (ea == null || eb == null) {
      return a[idx.fuel] === 'electric' && b[idx.fuel] === 'electric';
    }
    return Math.abs(ea - eb) <= cfg.engineTol;
  }

  function compute(maxPrice) {
    var out = [];
    market.forEach(function (r) {
      var price = r[idx.price_eur];
      if (price > maxPrice) return;  // candidate gate (custom budget)
      var group = groups[r[idx.make] + '|' + r[idx.model] + '|' + r[idx.fuel]] || [];
      var peers = group.filter(function (p) { return comparable(r, p); });
      var item = { r: r, comps: peers.length, score: null, good: false };
      if (peers.length >= cfg.minComps) {
        var med = median(peers.map(function (p) { return p[idx.price_eur]; }));
        var savings = med - price;
        var discount = 100 * savings / med;
        var medY = median(peers.map(function (p) { return p[idx.year]; }));
        var medM = median(peers.map(function (p) { return p[idx.mileage_km]; }));
        var mAdj = Math.max(-cfg.mileagePoints, Math.min(cfg.mileagePoints,
          cfg.mileagePoints * (medM - r[idx.mileage_km]) / cfg.mileageTol));
        var yAdj = Math.max(-cfg.yearPoints * cfg.yearTol, Math.min(cfg.yearPoints * cfg.yearTol,
          cfg.yearPoints * (r[idx.year] - medY)));
        item.median = med;
        item.savings = savings;
        item.discount = discount;
        item.score = Math.max(0, Math.min(cfg.scoreMax,
          Math.round(cfg.scoreCenter + cfg.discountMult * discount + mAdj + yAdj)));
        item.poolYear = Math.round(medY);
        item.poolMileage = Math.round(medM);
        item.good = discount >= cfg.goodDiscount && savings >= cfg.goodSavings;
      }
      out.push(item);
    });
    return out.filter(function (x) { return x.good; }).sort(function (a, b) {
      return (b.score - a.score) || ((b.savings || 0) - (a.savings || 0)) ||
             (a.r[idx.price_eur] - b.r[idx.price_eur]);
    });
  }

  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(v).toLocaleString('en-US');
  }

  function cell(text, sortVal, alignRight) {
    var td = document.createElement('td');
    td.style.padding = '6px';
    if (alignRight) td.style.textAlign = 'right';
    if (sortVal !== undefined && sortVal !== null) {
      td.setAttribute('data-sort', sortVal);
    }
    td.textContent = text;
    return td;
  }

  function render(qualified, maxPrice) {
    customView.innerHTML = '';
    var h2 = document.createElement('h2');
    h2.textContent = 'Within your €' + maxPrice.toLocaleString('en-US') +
                     ' budget — ' + qualified.length + ' qualifying deal(s)';
    customView.appendChild(h2);
    var note = document.createElement('p');
    note.className = 'note';
    note.textContent = 'Recomputed in your browser from today\\'s market snapshot (' +
      market.length + ' eligible listings). Same rules as the daily ranking; ' +
      'NEW / PRICE DROP badges and cross-source links appear in the default view only.';
    customView.appendChild(note);
    if (!qualified.length) {
      var p = document.createElement('p');
      p.textContent = 'No listing is at least ' + cfg.goodDiscount +
        '% (≥€' + cfg.goodSavings + ') below its comparable median within this budget today.';
      customView.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    table.id = 'car-custom-table';
    var headers = ['Listing', 'Asking', 'Median ask', 'Comps', 'Discount €', 'Discount %', 'Score'];
    var hr = document.createElement('tr');
    hr.style.background = '#f0f0f0';
    headers.forEach(function (name, col) {
      var th = document.createElement('th');
      th.className = 'sort-th';
      th.style.padding = '6px';
      if (col === 0) th.style.textAlign = 'left';
      th.textContent = name;
      th.onclick = (function (c) {
        return function () { sortTable('car-custom-table', c); };
      })(col);
      hr.appendChild(th);
    });
    table.appendChild(hr);
    qualified.forEach(function (item) {
      var r = item.r;
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.style.padding = '6px';
      td.setAttribute('data-sort', r[idx.make] + ' ' + r[idx.model]);
      td.appendChild(document.createTextNode(r[idx.make] + ' ' + r[idx.model]));
      td.appendChild(document.createElement('br'));
      var spec = document.createElement('span');
      spec.style.color = '#777';
      spec.style.fontSize = '12px';
      var parts = [r[idx.year], r[idx.fuel],
        r[idx.engine_l] != null ? r[idx.engine_l] + 'L' : '?L',
        r[idx.mileage_km] != null ? Math.round(r[idx.mileage_km] / 1000) + 'k km' : '? km'];
      if (r[idx.gearbox]) parts.push(r[idx.gearbox]);
      if (r[idx.body]) parts.push(r[idx.body]);
      spec.textContent = parts.join(' · ');
      td.appendChild(spec);
      td.appendChild(document.createElement('br'));
      if (r[idx.url]) {
        var a = document.createElement('a');
        a.href = r[idx.url];
        a.textContent = r[idx.source] || 'ad';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        td.appendChild(a);
      } else {
        td.appendChild(document.createTextNode(r[idx.source] || ''));
      }
      var cautions = [];
      if (r[idx.mileage_km] != null && r[idx.mileage_km] >= cfg.highMileageWarn) {
        cautions.push('High mileage — budget for repairs');
      }
      if (r[idx.year] != null && r[idx.year] <= thisYear - cfg.ageWarnYears) {
        cautions.push('Older car — inspect carefully');
      }
      if (cautions.length) {
        td.appendChild(document.createElement('br'));
        var c = document.createElement('span');
        c.style.color = '#a04000';
        c.style.fontSize = '12px';
        c.textContent = cautions.join('; ');
        td.appendChild(c);
      }
      tr.appendChild(td);
      tr.appendChild(cell(fmtEur(r[idx.price_eur]), r[idx.price_eur], true));
      var medTd = cell(fmtEur(item.median), item.median, true);
      if (item.poolYear != null) {
        medTd.appendChild(document.createElement('br'));
        var pn = document.createElement('span');
        pn.style.color = '#777';
        pn.style.fontSize = '11px';
        pn.textContent = 'pool ~' + item.poolYear + ' · ~' +
          Math.round(item.poolMileage / 1000) + 'k km';
        medTd.appendChild(pn);
      }
      tr.appendChild(medTd);
      tr.appendChild(cell(String(item.comps), item.comps, false));
      tr.appendChild(cell(fmtEur(item.savings), item.savings, true));
      tr.appendChild(cell(item.discount.toFixed(1) + '%', item.discount, true));
      var scTd = cell(String(item.score), item.score, false);
      scTd.style.fontWeight = 'bold';
      tr.appendChild(scTd);
      table.appendChild(tr);
    });
    customView.appendChild(table);
  }

  function showDefault() {
    customView.style.display = 'none';
    customView.innerHTML = '';
    defaultView.style.display = '';
    if (statusEl) statusEl.textContent = '';
  }

  var timer = null;
  function apply() {
    var raw = String(input.value || '').trim();
    var maxPrice = parseInt(raw, 10);
    if (!raw || isNaN(maxPrice)) { showDefault(); return; }
    maxPrice = Math.max(cfg.minPrice, Math.min(cfg.compMax, maxPrice));
    var t0 = (typeof performance !== 'undefined') ? performance.now() : Date.now();
    var qualified = compute(maxPrice);
    var ms = Math.round(((typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0));
    render(qualified, maxPrice);
    defaultView.style.display = 'none';
    customView.style.display = '';
    if (statusEl) {
      statusEl.textContent = qualified.length + ' deal(s) · recomputed in ' + ms + ' ms';
    }
  }

  input.addEventListener('input', function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 150);
  });
  var resetBtn = document.getElementById('car-budget-reset');
  if (resetBtn) resetBtn.addEventListener('click', function () {
    input.value = '';
    showDefault();
  });
  var okBtn = document.getElementById('car-budget-ok');
  if (okBtn) okBtn.addEventListener('click', function () {
    if (timer) clearTimeout(timer);
    apply();
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      if (timer) clearTimeout(timer);
      apply();
    }
  });
  var qs = (typeof location !== 'undefined' && location.search)
    ? location.search : '';
  var urlMax = new URLSearchParams(qs).get('max');
  if (urlMax && !isNaN(parseInt(urlMax, 10))) {
    input.value = urlMax;
    apply();
  }
  if (typeof window !== 'undefined') {
    window.__carBudget = { compute: compute, market: market, idx: idx, cfg: cfg };
  }
})();
"""


def _sort_val(v, default=-1):
    """Numeric value for a column's data-sort attribute (-1 when unknown,
    so unsortable rows sink rather than crash the JS parseFloat)."""
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return default


def _row(l, badges):
    key = f"{l.get('source')}:{l.get('id')}"
    title = l.get("title") or f"{l.get('make', '')} {l.get('model', '')}"
    cautions = []
    mileage = l.get("mileage_km")
    year = l.get("year")
    if isinstance(mileage, (int, float)) and \
            mileage >= config.CAR_HIGH_MILEAGE_WARNING_KM:
        cautions.append("High mileage — budget for repairs")
    if isinstance(year, int) and \
            year <= date.today().year - config.CAR_AGE_WARNING_YEARS:
        cautions.append("Older car — inspect carefully")
    caution_html = ""
    if cautions:
        caution_html = ("<br><span style='color:#a04000;font-size:12px'>"
                        f"{_e('; '.join(cautions))}</span>")
    sort_model = html.escape(
        f"{l.get('make') or ''} {l.get('model') or ''}".strip(), quote=True)
    cells = [
        f"<td style='padding:6px' data-sort='{sort_model}'>{_e(title)}<br>"
        f"<span style='color:#777;font-size:12px'>{_e(l.get('make'))} {_e(l.get('model'))} — {_spec_text(l)}</span><br>"
        f"{_badge_html(key, badges)} {_listing_links(l)}{caution_html}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('price_eur'))}'><b>{_fmt_eur(l.get('price_eur'))}</b></td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_median'))}'>{_fmt_eur(l.get('_median'))}{_pool_note(l)}</td>",
        f"<td style='padding:6px;text-align:center' data-sort='{_sort_val(l.get('_comps'))}'>{_fmt(l.get('_comps'))}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_savings'))}'>{_fmt_eur(l.get('_savings'))}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_discount_pct'))}'>{_fmt(l.get('_discount_pct'), '%')}</td>",
        f"<td style='padding:6px;text-align:center' data-sort='{_sort_val(l.get('_score'))}'><b>{_fmt(l.get('_score'))}</b></td>",
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _coverage_html(source_counts, source_errors):
    bits = []
    for name in ("ss.com", "pp.lv"):
        c = (source_counts or {}).get(name) or {}
        bits.append(f"{_e(name)}: {int(c.get('raw') or 0)} scraped, "
                    f"{int(c.get('eligible') or 0)} eligible")
    html_bits = "; ".join(bits)
    err = ""
    if source_errors:
        items = "".join(f"<li><b>{_e(src)}</b>: {_e(msg)}</li>"
                        for src, msg in source_errors.items())
        err = (f"<div style='background:#fdecea;border:1px solid #c0392b;"
               f"padding:10px 14px;margin:12px 0'>"
               f"<b>Warning: source outage — coverage is incomplete:</b><ul>{items}</ul></div>")
    reasons = ""
    drop = (source_counts or {}).get("_drop_reasons")
    if drop:
        reasons = ("<p class='note'>Dropped as ineligible: "
                   + ", ".join(f"{_e(k)} ×{v}" for k, v in sorted(drop.items()))
                   + "</p>")
    return html_bits, err, reasons


def build_html(qualified, assessed, source_counts, source_errors, badges, run_date,
               market=None):
    qualified = qualified or []
    assessed = assessed or []
    badges = badges or {}
    stamp = _riga_stamp(run_date)
    coverage, error_html, drop_html = _coverage_html(source_counts, source_errors)
    both_failed = source_errors and all(
        name in source_errors for name in ("ss.com", "pp.lv"))

    if both_failed:
        top_html = (
            "<h2 style='color:#c0392b'>Warning: no current data — both sources failed</h2>"
            "<p>Today's car scan could not reach either source, so there is "
            "<b>no fresh data</b> in this digest. This page is generated for "
            f"{_e(run_date)} and intentionally shows no listings rather than "
            "recycling an older digest.</p>")
    elif not qualified:
        top_html = (
            "<h2>No qualifying deals today</h2>"
            "<p>No listing is currently priced at least "
            f"{_e(config.CAR_GOOD_MIN_DISCOUNT_PCT)}% (≥{_fmt_eur(config.CAR_GOOD_MIN_SAVINGS_EUR)}) "
            "below the median of "
            f"{_e(config.CAR_MIN_COMPARABLES)}+ comparable asking prices, or "
            "there were too few comparable listings to compute a median. "
            f"Coverage: {coverage}.</p>")
    else:
        rows = "".join(_row(l, badges) for l in qualified)
        top_html = (
            f"<h2>All qualifying deals ({len(qualified)})</h2>"
            "<p class='note'>Click a column header to sort; click again to "
            "reverse. Ranking below is the default (score, then savings).</p>"
            "<table id='car-deals'><tr style='background:#f0f0f0'>"
            "<th class='sort-th' style='text-align:left;padding:6px' onclick=\"sortTable('car-deals', 0)\">Listing</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 1)\">Asking</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 2)\">Median ask</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 3)\">Comps</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 4)\">Discount €</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 5)\">Discount %</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 6)\">Score</th></tr>"
            f"{rows}</table>")

    # The custom-budget tool: embed today's full market snapshot and let the
    # browser re-rank for any budget in [min, compMax]. Without market data
    # (old callers / tests) the digest stays exactly as before.
    market_html = _market_data_html(market) if market else ""
    car_budget_script = (f'<script id="car-budget-js">{CAR_BUDGET_JS}</script>'
                         if market else "")
    budget_html = ""
    if market:
        budget_html = (
            "<div class='box' id='car-budget-box'>"
            "<b>Your budget:</b> "
            f"<input type='number' id='car-budget-input' min='{config.CAR_MIN_PRICE_EUR}' "
            f"max='{config.CAR_COMPARABLE_MAX_PRICE_EUR}' step='100' placeholder='e.g. 3500' "
            "style='padding:6px 8px;border:1px solid #b8c4cf;border-radius:4px;"
            "font-size:14px;width:110px'> "
            "<button type='button' id='car-budget-ok' style='padding:6px 10px;"
            "border:0;border-radius:4px;background:#2874a6;color:#fff;cursor:pointer;"
            "font-weight:bold'>OK</button> "
            "<button type='button' id='car-budget-reset' style='padding:6px 10px;"
            "border:0;border-radius:4px;background:#e7edf2;cursor:pointer;"
            "font-weight:bold'>Reset</button> "
            "<span class='note' id='car-budget-status'></span>"
            "<p class='note' style='margin:6px 0 0'>Enter a maximum price "
            f"(€{config.CAR_MIN_PRICE_EUR:,}–{config.CAR_COMPARABLE_MAX_PRICE_EUR:,}) "
            "and press <b>OK</b> (or Enter) to re-rank today's market snapshot "
            "for your budget — computed instantly in your browser from the "
            "data on this page, no rescraping (results also update as you "
            "type). <b>Reset</b> returns to the default daily view "
            f"(€{config.CAR_PRICE_CEILING_EUR:,} ceiling). Badges and "
            "cross-source links appear in the default view only. Shareable: "
            "append <b>?max=3500</b> to this page's URL.</p>"
            "</div>")
    if market:
        top_html = (f"<div id='car-default-view'>{top_html}</div>"
                    "<div id='car-custom-view' style='display:none'></div>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga car deals — {_e(run_date)}</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:960px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}h2{{color:#2874a6;margin-top:28px}}
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
</style>
<script>
// Click-to-sort table headers (same mechanism as the flats digest):
// first click sorts ascending, second click reverses. Numeric columns use
// the data-sort attribute on each cell; the Listing column falls back to
// string comparison of "make model".
var sortState = {{}};
function sortTable(tableId, colIdx) {{
  var table = document.getElementById(tableId);
  if (!table) return;
  var ths = table.querySelectorAll('th.sort-th');
  ths.forEach(function(th) {{ th.classList.remove('sort-asc','sort-desc'); }});
  var rows = Array.from(table.querySelectorAll('tr')).slice(1);
  var key = tableId + '_' + colIdx;
  sortState[key] = !sortState[key];
  var asc = sortState[key];
  var clickedTh = table.querySelectorAll('th')[colIdx];
  if (clickedTh) clickedTh.classList.add(asc ? 'sort-asc' : 'sort-desc');
  rows.sort(function(a, b) {{
    var va = a.children[colIdx].getAttribute('data-sort');
    var vb = b.children[colIdx].getAttribute('data-sort');
    if (va === null || vb === null) return 0;
    va = va.trim(); vb = vb.trim();
    var na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) {{
      return asc ? na - nb : nb - na;
    }}
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});
  rows.forEach(function(r) {{ table.appendChild(r); }});
}}
</script>
{market_html}
{car_budget_script}
</head><body>
<h1>Riga car deals — {_e(stamp)}</h1>
<p class="note">Coverage: {coverage}. ss.com: the newest
{_e(config.CAR_SS_MAX_PAGES_PER_MAKE)} pages per make, then a bounded deep
scan of up to {_e(config.CAR_SS_MAX_MODELS)} model pages (newest
{_e(config.CAR_SS_MAX_PAGES_PER_MODEL)} pages each, models chosen by
observed ad volume — no model is favoured); pp.lv: newest
{_e(config.CAR_PP_MAX_PAGES)} pages. This is <b>not</b> an exhaustive scan
of either site.</p>
{budget_html}
{error_html}
{drop_html}
<div class="box">
<b>Budget context (provisional):</b> on a ~€1,200/month income the
provisional purchase ceiling is {_fmt_eur(config.CAR_PRICE_CEILING_EUR)}
plus a <b>separate</b> {_fmt_eur(config.CAR_REPAIR_RESERVE_EUR)} reserve
intended for the pre-purchase inspection, initial repairs and
registration. Ongoing fuel, taxes, insurance and maintenance are
<b>additional</b> recurring expenses on top of that reserve. This ceiling
is a working assumption — not a guarantee of affordability and not
financing advice.
</div>
<div class="box">
<b>Eligibility:</b> only seller ads asking
{_fmt_eur(config.CAR_MIN_PRICE_EUR)}–{_fmt_eur(config.CAR_COMPARABLE_MAX_PRICE_EUR)}
with year ≥{_e(config.CAR_MIN_YEAR)} and mileage
≤{config.CAR_MAX_MILEAGE_KM:,} km, identified fuel (and engine where
applicable), are considered; candidates are capped at the provisional
{_fmt_eur(config.CAR_PRICE_CEILING_EUR)} ceiling, while comparable asking
prices are read up to {_fmt_eur(config.CAR_COMPARABLE_MAX_PRICE_EUR)}.
Ads without reliable specs are excluded or left unrated.
</div>
<div class="box">
<b>How a "top deal" is decided:</b> a listing qualifies only when its asking
price is at least {_e(config.CAR_GOOD_MIN_DISCOUNT_PCT)}% and
{_fmt_eur(config.CAR_GOOD_MIN_SAVINGS_EUR)} below the median of at least
{_e(config.CAR_MIN_COMPARABLES)} distinct comparable <i>current asking
prices</i>. Comparables must match on make/model (incl. generation), fuel,
year ±{_e(config.CAR_YEAR_TOLERANCE)}, mileage
±{config.CAR_MILEAGE_TOLERANCE_KM:,} km, engine
±{_e(config.CAR_ENGINE_TOLERANCE_L)}L, and gearbox/body whenever both
sides state them. Score is 0–100: 50 at the median, +1 point per 1%
cheaper, up to +{_e(config.CAR_SCORE_MILEAGE_POINTS)} points for mileage
below the pool's median (scaled across the
±{config.CAR_MILEAGE_TOLERANCE_KM:,} km window) and
+{_e(config.CAR_SCORE_YEAR_POINTS)} points per year newer than the pool's
median — so a cheap-but-worn car ranks below a cheap-and-fresh one. Missing
gearbox/body reduce confidence in the match. Asking prices are not final
selling prices, and mechanical/service condition cannot be verified from a
listing.
</div>
{top_html}
<div class="box">
<b>Before buying:</b> check mileage and history in the CSDD register
(e.csdd.lv), get an independent mechanical inspection, and verify all
documentation (registration, service records, outstanding finance).
</div>
<hr><p class="note">Generated by Flat_Searcher car digest — website only,
no email is sent for cars.</p>
</body></html>"""
