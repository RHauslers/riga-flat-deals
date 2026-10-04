# -*- coding: utf-8 -*-
"""
Car digest HTML builder — website only.

build_html(qualified, assessed, source_counts, source_errors, badges,
run_date) -> str
"""
import html
import json
from collections import Counter
from datetime import date
from urllib.parse import urlparse

import config
import utils
import web_style

ALLOWED_HOSTS = {"www.ss.com", "ss.com", "pp.lv", "www.pp.lv"}

BADGE_CLASSES = {
    "NEW": "b-new",
    "PRICE DROP": "b-down",
    "STILL ACTIVE": "b-ended",
    "REAPPEARED": "b-reg",
}


def _riga_stamp(run_date):
    return utils.riga_now_str()


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
    return utils.esc(value)


def _fmt(value, suffix=""):
    return f"{_e(value)}{suffix}" if value is not None else "—"


def _fmt_eur(value):
    return utils.fmt_eur(value)


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
    return web_style.badge(_e(b), BADGE_CLASSES.get(b, "b-src"))


def _listing_links(l):
    key_html = _link(l.get("url"), l.get("source") or "ad")
    for extra in l.get("also_on") or []:
        key_html += " + " + _link(extra.get("url"), extra.get("source") or "ad")
    return key_html


def _comps_html(l):
    """Comps cell; marks thin pools (< CAR_THIN_POOL_COMPS) — a median
    from 4 ads is much weaker evidence than one from 24."""
    n = l.get("_comps")
    txt = _fmt(n)
    try:
        thin = n is not None and int(n) < config.CAR_THIN_POOL_COMPS
    except (TypeError, ValueError):
        thin = False
    if not thin:
        return txt
    return (f"<span style='color:var(--warn)' title='Only {int(n)} comparable "
            f"ads — the median is less reliable'>{txt}~</span>")


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
    return (f"<br><span style='color:var(--muted);font-size:11px'>"
            f"pool ~{_e(year)} · ~{km}k km</span>")


# Fields embedded per listing so the browser can recompute deals for a
# custom budget (car_value.score_and_rank ported to JS). Order matters:
# the embedded JSON stores rows as arrays in this order. "_first_seen" and
# "_price_hist" are annotation fields cars.run() attaches from car_seen.json
# (days-in-scan and the [[date, price], ...] trail of ask changes).
_MARKET_FIELDS = ("source", "id", "make", "model", "year", "mileage_km",
                  "fuel", "engine_l", "gearbox", "body", "price_eur", "url",
                  "_first_seen", "_price_hist")

# Repeated string columns are stored once in payload["dict"] with int
# indices in the rows — make/model/fuel/gearbox/body/source/_first_seen
# together were ~40% of the ~800 KB embed. The budget/watch JS decodes
# them back to real strings at init, before anything reads the rows.
_MARKET_DICT_FIELDS = ("source", "make", "model", "fuel", "gearbox",
                       "body", "_first_seen")


def _sparkline(hist, w=64, h=16):
    """Tiny inline-SVG price trail (red = dropping, green = rising, grey =
    flat). All values are numbers we generated, so no escaping needed."""
    return utils.sparkline_svg(hist, w, h)


def _history_html(l, run_date=None):
    """'seen N d' + ask-price trail under a listing. "Seen" is the first day
    our own scan observed the ad (≈ days on the market, bounded by how long
    we have been tracking)."""
    bits = []
    first = l.get("_first_seen")
    if first:
        try:
            end = date.fromisoformat(str(run_date)) if run_date else date.today()
            days = (end - date.fromisoformat(str(first))).days
            bits.append(f"seen {days} d" if days > 0 else "seen today")
        except ValueError:
            pass
    hist = []
    for point in l.get("_price_hist") or []:
        try:
            hist.append((str(point[0]), float(point[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(hist) >= 2:
        trail = " → ".join(_fmt_eur(p) for _, p in hist[-4:])
        if len(hist) > 4:
            trail = "… " + trail
        tip = html.escape("\n".join(f"{d}: €{int(round(p)):,}"
                                    for d, p in hist), quote=True)
        bits.append(f'<span title="{tip}">{trail}</span>')
    if not bits:
        return ""
    return ("<br><span style='color:var(--muted);font-size:12px'>"
            + " · ".join(bits) + _sparkline(hist) + "</span>")


def _market_data_html(market):
    """Embed the day's deduplicated eligible market (all prices up to the
    comparable ceiling, not just today's candidates) as JSON, with the
    scoring config, so the page can re-rank for any budget in the browser.
    URLs are allow-listed here — the same rule _link() applies to rows."""
    url_i = _MARKET_FIELDS.index("url")
    dict_idx = {f: _MARKET_FIELDS.index(f) for f in _MARKET_DICT_FIELDS}
    dicts = {f: [] for f in _MARKET_DICT_FIELDS}
    dict_map = {f: {} for f in _MARKET_DICT_FIELDS}
    rows = []
    for l in market or []:
        row = [l.get(f) for f in _MARKET_FIELDS]
        if not _safe_url(row[url_i]):
            row[url_i] = ""
        for f, i in dict_idx.items():
            v = row[i]
            if v is None:
                continue
            idx = dict_map[f].get(v)
            if idx is None:
                idx = len(dicts[f])
                dict_map[f][v] = idx
                dicts[f].append(v)
            row[i] = idx
        rows.append(row)
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
        "thinPool": config.CAR_THIN_POOL_COMPS,
        "goodDiscount": config.CAR_GOOD_MIN_DISCOUNT_PCT,
        "goodSavings": config.CAR_GOOD_MIN_SAVINGS_EUR,
        "scoreCenter": config.CAR_SCORE_CENTER,
        "discountMult": config.CAR_SCORE_DISCOUNT_MULTIPLIER,
        "mileagePoints": config.CAR_SCORE_MILEAGE_POINTS,
        "yearPoints": config.CAR_SCORE_YEAR_POINTS,
        "scoreMax": config.CAR_SCORE_MAX,
        "fuels": ["petrol", "diesel", "hybrid", "electric", "lpg"],
    }
    payload = {"config": cfg, "fields": list(_MARKET_FIELDS),
               "dict": dicts, "rows": rows}
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
// Runs after the DOM is ready: the budget input lives in the body
// element, below this script in the head, so it does not exist at parse
// time. (No literal '<body>' here — website._inject_nav searches for it.)
function __carBudgetInit() {
  var dataEl = document.getElementById('car-market-data');
  var input = document.getElementById('car-budget-input');
  var statusEl = document.getElementById('car-budget-status');
  var defaultView = document.getElementById('car-default-view');
  var customView = document.getElementById('car-custom-view');
  if (!dataEl || !input || !defaultView || !customView) return;
  var payload = JSON.parse(dataEl.textContent);
  var cfg = payload.config, F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  // Dictionary-encoded string columns -> real strings, before any reads.
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      payload.rows.forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  var thisYear = new Date().getFullYear();
  var makeSel = document.getElementById('car-filter-make');
  var fuelSel = document.getElementById('car-filter-fuel');
  var gbSel = document.getElementById('car-filter-gearbox');
  var yearInput = document.getElementById('car-filter-year');
  var kmInput = document.getElementById('car-filter-km');
  var modelInput = document.getElementById('car-filter-model');
  var minInput = document.getElementById('car-filter-min');

  // 'Passat B7' / 'passat-b7' / 'passat b7' must all match: compare on
  // alphanumeric-only lowercase forms.
  function norm(s) {
    return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, '');
  }

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

  // Fill the make dropdown from today's data.
  if (makeSel) {
    var seen = {};
    market.forEach(function (r) { if (r[idx.make]) seen[r[idx.make]] = 1; });
    Object.keys(seen).sort().forEach(function (m) {
      var o = document.createElement('option');
      o.value = m;
      o.textContent = m.charAt(0).toUpperCase() + m.slice(1);
      makeSel.appendChild(o);
    });
  }

  // Filters narrow which listings may become candidates; the comparable
  // pools still cover the whole market (same rule as the budget itself).
  function readFilters() {
    return {
      make: makeSel && makeSel.value ? makeSel.value : '',
      model: modelInput && modelInput.value ? modelInput.value : '',
      fuel: fuelSel && fuelSel.value ? fuelSel.value : '',
      gearbox: gbSel && gbSel.value ? gbSel.value : '',
      minYear: yearInput ? (parseInt(yearInput.value, 10) || 0) : 0,
      maxKm: kmInput ? (parseInt(kmInput.value, 10) || 0) : 0,
      minPrice: minInput ? (parseInt(minInput.value, 10) || 0) : 0
    };
  }

  function anyFilterSet(f) {
    return !!(f.make || f.model || f.fuel || f.gearbox || f.minYear ||
              f.maxKm || f.minPrice);
  }

  function passesFilters(r, f) {
    if (f.make && r[idx.make] !== f.make) return false;
    if (f.model && norm(r[idx.model]).indexOf(norm(f.model)) < 0)
      return false;
    if (f.minPrice && (r[idx.price_eur] == null ||
                       r[idx.price_eur] < f.minPrice)) return false;
    if (f.fuel && r[idx.fuel] !== f.fuel) return false;
    if (f.gearbox && r[idx.gearbox] !== f.gearbox) return false;
    if (f.minYear && (r[idx.year] == null || r[idx.year] < f.minYear)) return false;
    if (f.maxKm && (r[idx.mileage_km] == null || r[idx.mileage_km] > f.maxKm)) return false;
    return true;
  }

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

  function compute(maxPrice, filters) {
    filters = filters || {};
    var out = [];
    market.forEach(function (r) {
      var price = r[idx.price_eur];
      if (price > maxPrice) return;  // candidate gate (custom budget)
      if (!passesFilters(r, filters)) return;  // make/fuel/gearbox/year/km
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
    // All matching candidates are returned (not only qualifying ones) —
    // a filtered view that shows "0 qualifying" without the rest of the
    // market is misleading: e.g. every Passat at market price.
    return out.sort(function (a, b) {
      return ((b.score == null ? -1 : b.score) - (a.score == null ? -1 : a.score)) ||
             ((b.savings || 0) - (a.savings || 0)) ||
             (a.r[idx.price_eur] - b.r[idx.price_eur]);
    });
  }

  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(v).toLocaleString('en-US');
  }

  // Tiny price-trail sparkline (same shape as the Python one).
  function sparkEl(hist) {
    if (typeof document.createElementNS === 'undefined') return null;
    var pts = [];
    (hist || []).forEach(function (h) {
      var v = Number(h[1]);
      if (!isNaN(v)) pts.push(v);
    });
    if (pts.length < 2) return null;
    var w = 64, h = 16;
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    var span = (hi - lo) || 1;
    var coords = pts.map(function (v, i) {
      return (i * (w - 4) / (pts.length - 1) + 2).toFixed(1) + ',' +
             (h - 3 - (v - lo) / span * (h - 6)).toFixed(1);
    }).join(' ');
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', w); svg.setAttribute('height', h);
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.style.verticalAlign = '-3px'; svg.style.marginLeft = '4px';
    var pl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    pl.setAttribute('points', coords); pl.setAttribute('fill', 'none');
    pl.setAttribute('stroke', pts[pts.length - 1] < pts[0] ? 'var(--bad)'
      : (pts[pts.length - 1] > pts[0] ? 'var(--good)' : 'var(--muted)'));
    pl.setAttribute('stroke-width', '1.5');
    svg.appendChild(pl);
    return svg;
  }

  // 'seen N d' + '€5,500 → €4,900' line (matches _history_html in Python).
  function appendHistory(td, r) {
    var bits = [];
    var first = r[idx._first_seen];
    if (first) {
      var t = Date.parse(String(first) + 'T00:00:00Z');
      if (!isNaN(t)) {
        var days = Math.max(0, Math.round((Date.now() - t) / 86400000));
        bits.push(days > 0 ? 'seen ' + days + ' d' : 'seen today');
      }
    }
    var hist = r[idx._price_hist] || [];
    if (hist.length >= 2) {
      var tail = hist.slice(-4).map(function (h) { return fmtEur(h[1]); });
      bits.push((hist.length > 4 ? '… ' : '') + tail.join(' → '));
    }
    if (!bits.length) return;
    td.appendChild(document.createElement('br'));
    var s = document.createElement('span');
    s.style.color = 'var(--muted)'; s.style.fontSize = '12px';
    s.textContent = bits.join(' · ');
    td.appendChild(s);
    var spark = sparkEl(hist);
    if (spark) td.appendChild(spark);
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

  function describeFilters(f) {
    var parts = [];
    if (f.make) parts.push('make=' + f.make);
    if (f.model) parts.push('model ~' + f.model);
    if (f.fuel) parts.push('fuel=' + f.fuel);
    if (f.gearbox) parts.push('gearbox=' + f.gearbox);
    if (f.minYear) parts.push('year ≥' + f.minYear);
    if (f.maxKm) parts.push('km ≤' + f.maxKm.toLocaleString('en-US'));
    if (f.minPrice) parts.push('min €' + f.minPrice.toLocaleString('en-US'));
    return parts.join(', ');
  }

  function render(items, qualified, maxPrice, defaultCeiling, filters) {
    customView.innerHTML = '';
    var fdesc = filters ? describeFilters(filters) : '';
    var h2 = document.createElement('h2');
    h2.textContent = (defaultCeiling
      ? 'Within the default €' + maxPrice.toLocaleString('en-US') + ' ceiling'
      : 'Within your €' + maxPrice.toLocaleString('en-US') + ' budget') +
      (fdesc ? ' · ' + fdesc : '') +
      ' — ' + qualified.length + ' qualifying deal(s)' +
      (items.length > qualified.length
        ? ' of ' + items.length + ' matching' : '');
    customView.appendChild(h2);
    var note = document.createElement('p');
    note.className = 'note';
    note.textContent = 'Every matching listing from the market snapshot (' +
      market.length + ' eligible), scored against its comparable pool — ' +
      'green score = qualifying deal, “—” = too few comps to appraise. ' +
      'Badges and cross-source links appear in the default view only.';
    customView.appendChild(note);
    if (!items.length) {
      var p = document.createElement('p');
      p.textContent = 'No matching listing within this budget today.';
      customView.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    table.id = 'car-custom-table';
    var headers = ['Listing', 'Asking', 'Median ask', 'Comps', 'Discount €', 'Discount %', 'Score'];
    var hr = document.createElement('tr');
    hr.style.background = 'var(--line)';
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
    items.forEach(function (item) {
      var r = item.r;
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.style.padding = '6px';
      td.setAttribute('data-sort', r[idx.make] + ' ' + r[idx.model]);
      var star = document.createElement('button');
      star.type = 'button';
      star.className = 'watch-star';
      star.setAttribute('data-key', r[idx.source] + ':' + r[idx.id]);
      star.setAttribute('data-label',
        (String(r[idx.make] || '') + ' ' + String(r[idx.model] || ''))
          .replace(/-/g, ' ').trim() || 'car');
      star.setAttribute('data-price', r[idx.price_eur]);
      star.setAttribute('data-url', r[idx.url] || '');
      star.title = 'Watch this listing';
      star.textContent = '☆';
      td.appendChild(star);
      td.appendChild(document.createTextNode(' '));
      td.appendChild(document.createTextNode(r[idx.make] + ' ' + r[idx.model]));
      td.appendChild(document.createElement('br'));
      var spec = document.createElement('span');
      spec.style.color = 'var(--muted)';
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
        c.style.color = 'var(--bad)';
        c.style.fontSize = '12px';
        c.textContent = cautions.join('; ');
        td.appendChild(c);
      }
      appendHistory(td, r);
      tr.appendChild(td);
      tr.appendChild(cell(fmtEur(r[idx.price_eur]), r[idx.price_eur], true));
      var medTd = cell(fmtEur(item.median), item.median, true);
      if (item.poolYear != null) {
        medTd.appendChild(document.createElement('br'));
        var pn = document.createElement('span');
        pn.style.color = 'var(--muted)';
        pn.style.fontSize = '11px';
        pn.textContent = 'pool ~' + item.poolYear + ' · ~' +
          Math.round(item.poolMileage / 1000) + 'k km';
        medTd.appendChild(pn);
      }
      tr.appendChild(medTd);
      var compsTd = cell(String(item.comps), item.comps, false);
      if (item.comps < cfg.thinPool) {
        compsTd.title = 'Only ' + item.comps + ' comparable ads — ' +
          'the median is less reliable';
        compsTd.style.color = 'var(--warn)';
        compsTd.appendChild(document.createTextNode('~'));
      }
      tr.appendChild(compsTd);
      tr.appendChild(cell(fmtEur(item.savings), item.savings, true));
      tr.appendChild(cell(item.discount != null
        ? item.discount.toFixed(1) + '%' : '—', item.discount, true));
      var scTd = cell(item.score != null ? String(item.score) : '—',
        item.score, false);
      scTd.style.fontWeight = 'bold';
      if (item.good) scTd.style.color = 'var(--good)';
      tr.appendChild(scTd);
      table.appendChild(tr);
    });
    customView.appendChild(table);
    if (typeof window !== 'undefined' && window.__carWatchRefresh) {
      window.__carWatchRefresh();
    }
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
    var filters = readFilters();
    var filtered = anyFilterSet(filters);
    if ((!raw || isNaN(maxPrice)) && !filtered) { showDefault(); return; }
    var defaultCeiling = isNaN(maxPrice);  // filters set, no budget typed
    if (defaultCeiling) maxPrice = cfg.defaultMax;
    maxPrice = Math.max(cfg.minPrice, Math.min(cfg.compMax, maxPrice));
    var t0 = (typeof performance !== 'undefined') ? performance.now() : Date.now();
    var items = compute(maxPrice, filters);
    var qualified = items.filter(function (x) { return x.good; });
    var ms = Math.round(((typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0));
    render(items, qualified, maxPrice, defaultCeiling, filters);
    defaultView.style.display = 'none';
    customView.style.display = '';
    if (statusEl) {
      var fdesc = describeFilters(filters);
      statusEl.textContent = qualified.length + ' deal(s) of ' +
        items.length + ' matching' + (fdesc ? ' · ' + fdesc : '') +
        ' · recomputed in ' + ms + ' ms';
    }
  }

  input.addEventListener('input', function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 150);
  });
  function scheduleApply() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 150);
  }
  [makeSel, fuelSel, gbSel].forEach(function (el) {
    if (el) el.addEventListener('change', scheduleApply);
  });
  [yearInput, kmInput, modelInput, minInput].forEach(function (el) {
    if (el) el.addEventListener('input', scheduleApply);
  });
  var resetBtn = document.getElementById('car-budget-reset');
  if (resetBtn) resetBtn.addEventListener('click', function () {
    input.value = '';
    [makeSel, fuelSel, gbSel, yearInput, kmInput, modelInput, minInput]
      .forEach(function (el) {
        if (el) el.value = '';
      });
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
  var params = new URLSearchParams(qs);
  var urlMax = params.get('max');
  if (urlMax && !isNaN(parseInt(urlMax, 10))) input.value = urlMax;
  function urlSet(name, el) {
    var v = params.get(name);
    if (v && el) { el.value = v; return true; }
    return false;
  }
  var urlActive = !!urlMax;
  urlActive = urlSet('make', makeSel) || urlActive;
  urlActive = urlSet('model', modelInput) || urlActive;
  urlActive = urlSet('min', minInput) || urlActive;
  urlActive = urlSet('fuel', fuelSel) || urlActive;
  urlActive = urlSet('gearbox', gbSel) || urlActive;
  urlActive = urlSet('year', yearInput) || urlActive;
  urlActive = urlSet('km', kmInput) || urlActive;
  if (urlActive) apply();
  if (typeof window !== 'undefined') {
    window.__carBudget = { compute: compute, apply: apply, market: market,
                           idx: idx, cfg: cfg, readFilters: readFilters };
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __carBudgetInit);
} else {
  __carBudgetInit();
}
"""


# Watchlist: ☆/★ buttons on deal rows persist picks in localStorage
# (key watch_cars_v1). A watched listing missing from today's embedded
# market data is flagged "no longer listed" (sold or ad expired).
CAR_WATCH_JS = """
function __carWatchInit() {
  var dataEl = document.getElementById('car-market-data');
  var box = document.getElementById('car-watch-box');
  var listEl = document.getElementById('car-watch-list');
  var countEl = document.getElementById('car-watch-count');
  if (!dataEl || !box || !listEl) return;
  if (typeof localStorage === 'undefined') return;
  var payload = JSON.parse(dataEl.textContent);
  var F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      payload.rows.forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  var byKey = {};
  payload.rows.forEach(function (r) {
    if (r[idx.source] != null && r[idx.id] != null)
      byKey[r[idx.source] + ':' + r[idx.id]] = r;
  });
  var KEY = 'watch_cars_v1';
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; }
    catch (e) { return {}; }
  }
  function save(w) {
    try { localStorage.setItem(KEY, JSON.stringify(w)); } catch (e) {}
  }
  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(Number(v)).toLocaleString('en-US');
  }

  function refreshStars() {
    if (!document.querySelectorAll) return;
    var w = load();
    Array.prototype.forEach.call(
      document.querySelectorAll('.watch-star'), function (s) {
        s.textContent = w[s.getAttribute('data-key')] ? '★' : '☆';
      });
  }

  function toggle(btn) {
    var key = btn.getAttribute('data-key');
    if (!key) return;
    var w = load();
    if (w[key]) {
      delete w[key];
    } else {
      w[key] = {
        added: new Date().toISOString().slice(0, 10),
        label: btn.getAttribute('data-label') || key,
        price: parseFloat(btn.getAttribute('data-price')),
        url: btn.getAttribute('data-url') || ''
      };
    }
    save(w);
    refreshStars();
    renderBox();
  }

  function remove(key) {
    var w = load();
    delete w[key];
    save(w);
    refreshStars();
    renderBox();
  }

  function renderBox() {
    var w = load();
    var keys = Object.keys(w).sort(function (a, b) {
      return String(w[b].added || '').localeCompare(String(w[a].added || ''));
    });
    if (countEl) countEl.textContent = '(' + keys.length + ')';
    listEl.innerHTML = '';
    if (!keys.length) {
      var p = document.createElement('p');
      p.className = 'note';
      p.textContent = 'Nothing starred yet — click ☆ on a listing to pin it here.';
      listEl.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    keys.forEach(function (key) {
      var w0 = w[key];
      var cur = byKey[key];
      var tr = document.createElement('tr');
      var td = document.createElement('td');
      td.style.padding = '6px';
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'watch-remove';
      rm.setAttribute('data-key', key);
      rm.title = 'Stop watching';
      rm.textContent = '✕';
      rm.style.cssText = 'border:0;background:none;color:var(--bad);cursor:pointer;margin-right:6px';
      td.appendChild(rm);
      var label = w0.label || key;
      var url = (cur && cur[idx.url]) ? cur[idx.url] : (w0.url || '');
      if (url) {
        var a = document.createElement('a');
        a.href = url;
        a.textContent = label;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        td.appendChild(a);
      } else {
        td.appendChild(document.createTextNode(label));
      }
      var meta = document.createElement('span');
      meta.style.fontSize = '12px';
      var delta = null;
      if (cur) {
        meta.style.color = 'var(--muted)';
        meta.textContent = ' — ' + fmtEur(cur[idx.price_eur]) +
          ' · still listed today';
        var p0 = Number(w0.price), p1 = Number(cur[idx.price_eur]);
        if (isFinite(p0) && isFinite(p1) && Math.abs(p1 - p0) >= 1) {
          delta = document.createElement('span');
          delta.style.color = p1 < p0 ? 'var(--good)' : 'var(--bad)';
          delta.style.fontWeight = 'bold';
          delta.style.fontSize = '12px';
          delta.textContent = ' ' + (p1 < p0 ? '▼' : '▲') + ' ' +
            fmtEur(Math.abs(p1 - p0)) + ' since starred';
        }
      } else {
        meta.style.color = 'var(--bad)';
        meta.textContent = ' — last seen ' + fmtEur(w0.price) +
          ' · NO LONGER LISTED (sold or expired)';
      }
      td.appendChild(meta);
      if (delta) td.appendChild(delta);
      var since = document.createElement('span');
      since.style.color = 'var(--faint)';
      since.style.fontSize = '11px';
      since.textContent = ' · watching since ' + (w0.added || '?');
      td.appendChild(since);
      tr.appendChild(td);
      table.appendChild(tr);
    });
    listEl.appendChild(table);
  }

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.getAttribute || !t.classList) return;
    if (t.classList.contains('watch-star')) { toggle(t); }
    else if (t.classList.contains('watch-remove')) {
      remove(t.getAttribute('data-key'));
    }
  });
  refreshStars();
  renderBox();
  if (typeof window !== 'undefined') {
    window.__carWatch = { toggle: toggle, renderBox: renderBox, load: load };
    window.__carWatchRefresh = refreshStars;
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __carWatchInit);
} else {
  __carWatchInit();
}
"""


def _sort_val(v, default=-1):
    """Numeric value for a column's data-sort attribute (-1 when unknown,
    so unsortable rows sink rather than crash the JS parseFloat)."""
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return default


def _motivated_chip(l, run_date=None):
    """'−€X' drop chip + amber MOTIVATED pill when the car's recorded
    ask-price trail shows a real cut plus staleness or repeated cuts."""
    info = utils.car_motivated(l, today=run_date)
    if not info or not info.get("drop_eur"):
        return ""
    bits = [f"<span class='badge b-cheap' "
            f"title='Asking price cut since first seen'>"
            f"−{_fmt_eur(info['drop_eur'])}</span>"]
    if utils.is_motivated(info, config.MOTIVATED_STALE_DAYS_CAR,
                          config.MOTIVATED_MIN_DROP_EUR_CAR):
        why = []
        if info.get("days", 0) >= config.MOTIVATED_STALE_DAYS_CAR:
            why.append(f"seen {info['days']} days")
        if info.get("trail_drops", 0) >= config.MOTIVATED_MIN_TRAIL_DROPS:
            why.append(f"{info['trail_drops']} cuts")
        bits.append(f"<span class='badge b-mot' "
                    f"title='Seller may be negotiable: "
                    f"{_e('; '.join(why))}'>MOTIVATED</span>")
    if info.get("at_low"):
        bits.append(f"<span class='badge b-low' "
                    f"title='Cheapest ask we have ever observed for "
                    f"this car — best moment to offer'>LOWEST SEEN</span>")
    return " " + " ".join(bits)


def build_cuts_html(assessed, run_date=None, top_n=None):
    """'Biggest price cuts' card — cars whose recorded ask trail shows the
    largest drops. Runs on all assessed cars so cuts on non-qualifying
    ads are still visible."""
    if not assessed:
        return ""
    top_n = top_n or config.MOTIVATED_CUTS_TOP_N
    cuts = []
    for l in assessed:
        info = utils.car_motivated(l, today=run_date)
        if info and info.get("drop_eur", 0) >= config.MOTIVATED_MIN_DROP_EUR_CAR:
            cuts.append((l, info))
    if not cuts:
        return ""
    cuts.sort(key=lambda x: -x[1]["drop_eur"])
    cuts = cuts[:top_n]
    rows = []
    for l, info in cuts:
        mot = (utils.is_motivated(info, config.MOTIVATED_STALE_DAYS_CAR,
                                  config.MOTIVATED_MIN_DROP_EUR_CAR)
               and " <span class='badge b-mot' "
                   "title='Stale listing + real cut: seller may be "
                   "negotiable'>MOTIVATED</span>" or "")
        url = _safe_url(l.get('url'))
        title = _e(f"{l.get('make','')} {l.get('model','')} "
                   f"{l.get('title') or ''}".strip())
        title_cell = f"<a href='{url}'>{title}</a>" if url else title
        days = f"{info['days']}d" if info.get("days") else "?"
        src = _e(l.get('source', ''))
        rows.append(
            f"<tr>"
            f"<td data-sort='{_e(title)}'>{title_cell}<br>"
            f"<span style='color:var(--muted);font-size:12px'>"
            f"{_e(l.get('year',''))} · {_spec_text(l)}</span></td>"
            f"<td data-sort='{info['drop_eur']:.0f}' "
            f"style='text-align:right'>"
            f"<span style='color:var(--muted)'>{_fmt_eur(info['was'])}</span>"
            f" → <b>{_fmt_eur(info['now'])}</b></td>"
            f"<td data-sort='{info['drop_pct']:.1f}' "
            f"style='text-align:right;color:var(--good);font-weight:bold'>"
            f"−{_fmt_eur(info['drop_eur'])} (−{info['drop_pct']:.0f}%)</td>"
            f"<td data-sort='{info.get('days',0)}' "
            f"style='text-align:right'>{days}</td>"
            f"<td>{src}{mot}</td>"
            "</tr>")
    return (
        "<div class='card'>"
        "<h3 style='color:var(--accent);border:none;margin:0 0 4px 0'>"
        "Biggest price cuts</h3>"
        "<p style='color:var(--muted);font-size:12px;margin:0 0 8px 0'>"
        "Cars that cut their asking price since we first saw them — a "
        "<span class='badge b-mot'>MOTIVATED</span> seller is likely "
        "negotiable (stale listing or repeated cuts).</p>"
        "<div class='scroll-x'><table id='car-cuts'>"
        "<tr><th style='text-align:left'>Listing</th><th>Was → Now</th>"
        "<th>Cut</th><th>Seen</th><th>Source</th></tr>"
        + "".join(rows) +
        "</table></div></div>")


def _row(l, badges, run_date=None):
    key = utils.listing_key(l)
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
        caution_html = ("<br><span style='color:var(--bad);font-size:12px'>"
                        f"{_e('; '.join(cautions))}</span>")
    sort_model = html.escape(
        f"{l.get('make') or ''} {l.get('model') or ''}".strip(), quote=True)
    star_label = (f"{(l.get('make') or '').title()} "
                  f"{(l.get('model') or '').replace('-', ' ').title()}").strip()
    star = (
        f"<button type='button' class='watch-star' "
        f"data-key='{_e(key)}' data-label='{_e(star_label or 'car')}' "
        f"data-price='{_e(l.get('price_eur'))}' "
        f"data-url='{_e(_safe_url(l.get('url')) or '')}' "
        f"title='Watch this listing'>☆</button> ")
    cells = [
        f"<td style='padding:6px' data-sort='{sort_model}'>{star}{_e(title)}<br>"
        f"<span style='color:var(--muted);font-size:12px'>{_e(l.get('make'))} {_e(l.get('model'))} — {_spec_text(l)}</span><br>"
        f"{_badge_html(key, badges)} {_listing_links(l)}"
        f"{_motivated_chip(l, run_date)}{caution_html}"
        f"{_history_html(l)}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('price_eur'))}'><b>{_fmt_eur(l.get('price_eur'))}</b></td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_median'))}'>{_fmt_eur(l.get('_median'))}{_pool_note(l)}</td>",
        f"<td style='padding:6px;text-align:center' data-sort='{_sort_val(l.get('_comps'))}'>{_comps_html(l)}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_savings'))}'>{_fmt_eur(l.get('_savings'))}</td>",
        f"<td style='padding:6px;text-align:right' data-sort='{_sort_val(l.get('_discount_pct'))}'>{_fmt(l.get('_discount_pct'), '%')}</td>",
        f"<td style='padding:6px;text-align:center' data-sort='{_sort_val(l.get('_score'))}'><b>{_fmt(l.get('_score'))}</b></td>",
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _gone_html(gone, seen, run_date):
    """'Gone since yesterday' — ads in yesterday's snapshot missing from
    today's deduped pool (sold or withdrawn; source-scoped so a failed
    scraper can't mass-report). '' when nothing vanished."""
    if not gone:
        return ""
    seen = seen or {}
    try:
        today_d = date.fromisoformat(str(run_date)) if run_date \
            else date.today()
    except ValueError:
        today_d = date.today()
    rows = []
    for i, r in enumerate(gone):
        zebra = " class='z'" if i % 2 else ""
        entry = seen.get(r.get("k")) or {}
        days = utils.days_since(entry.get("first_seen"), today_d)
        tracked = f"{days} d" if days is not None else "?"
        url = _safe_url(r.get("u"))
        link = (f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">view</a>') if url else "—"
        label = " ".join(x for x in
                         (_e(r.get("mk")), _e(r.get("mo")),
                          _e(r.get("y"))) if x)
        rows.append(
            f"<tr{zebra}>"
            f"<td style='padding:6px'>{label or '—'}</td>"
            f"<td style='padding:6px;text-align:right;font-weight:bold'>"
            f"{_fmt_eur(r.get('p'))}</td>"
            f"<td style='padding:6px;text-align:right;color:var(--muted)'>"
            f"{tracked}</td>"
            f"<td style='padding:6px;font-size:12px;color:var(--faint)'>"
            f"{_e(r.get('k', '').split(':', 1)[0])}</td>"
            f"<td style='padding:6px'>{link}</td></tr>")
    return (
        "<div class='box' style='background:var(--warn-bg);border-color:var(--warn-line)'>"
        f"<b>Gone since yesterday ({len(gone)})</b> — ads that were live "
        "yesterday but are no longer listed (usually sold or withdrawn). "
        "'Tracked' is how long our scan had seen the ad."
        f"<div class='scroll-x'><table><tr>"
        "<th style='text-align:left;padding:6px'>Car</th>"
        "<th style='text-align:right;padding:6px'>Last ask</th>"
        "<th style='text-align:right;padding:6px'>Tracked</th>"
        "<th style='padding:6px'>Source</th><th style='padding:6px'>Link</th>"
        f"</tr>{''.join(rows)}</table></div></div>")


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
        err = ("<div class='warn'>"
               "<b>Warning: source outage — coverage is incomplete:</b>"
               f"<ul>{items}</ul></div>")
    reasons = ""
    drop = (source_counts or {}).get("_drop_reasons")
    if drop:
        reasons = ("<p class='note'>Dropped as ineligible: "
                   + ", ".join(f"{_e(k)} ×{v}" for k, v in sorted(drop.items()))
                   + "</p>")
    return html_bits, err, reasons


def build_html(qualified, assessed, source_counts, source_errors, badges, run_date,
               market=None, gone=None, seen=None):
    qualified = qualified or []
    assessed = assessed or []
    badges = badges or {}
    stamp = _riga_stamp(run_date)
    coverage, error_html, drop_html = _coverage_html(source_counts, source_errors)
    both_failed = source_errors and all(
        name in source_errors for name in ("ss.com", "pp.lv"))

    if both_failed:
        top_html = (
            "<h2 style='color:var(--bad)'>Warning: no current data — both sources failed</h2>"
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
        rows = "".join(_row(l, badges, run_date) for l in qualified)
        badge_counts = Counter(badges.values())
        badge_bits = " · ".join(
            f"{badge_counts[b]} {b.lower()}"
            for b in ("NEW", "PRICE DROP", "REAPPEARED", "STILL ACTIVE")
            if badge_counts.get(b))
        badge_line = (f"<p class='note' style='margin-top:0'>Today: "
                      f"{badge_bits}.</p>" if badge_bits else "")
        top_html = (
            f"<h2>All qualifying deals ({len(qualified)})</h2>"
            f"{badge_line}"
            "<p class='note'>Click a column header to sort; click again to "
            "reverse. Ranking below is the default (score, then savings). "
            f"A Comps value with <b style='color:var(--warn)'>~</b> means fewer "
            f"than {_e(config.CAR_THIN_POOL_COMPS)} comparable ads — the "
            "median for that row is less reliable.</p>"
            "<div class='card'><div class='scroll-x'>"
            "<table id='car-deals'><tr>"
            "<th class='sort-th' style='text-align:left;padding:6px' onclick=\"sortTable('car-deals', 0)\">Listing</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 1)\">Asking</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 2)\">Median ask</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 3)\">Comps</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 4)\">Discount €</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 5)\">Discount %</th>"
            "<th class='sort-th' style='padding:6px' onclick=\"sortTable('car-deals', 6)\">Score</th></tr>"
            f"{rows}</table></div></div>")

    # The custom-budget tool: embed today's full market snapshot and let the
    # browser re-rank for any budget in [min, compMax]. Without market data
    # (old callers / tests) the digest stays exactly as before.
    market_html = _market_data_html(market) if market else ""
    car_budget_script = (f'<script id="car-budget-js">{CAR_BUDGET_JS}</script>'
                         if market else "")
    car_watch_script = (f'<script id="car-watch-js">{CAR_WATCH_JS}</script>'
                        if market else "")
    budget_html = ""
    if market:
        budget_html = (
            "<div class='box' id='car-budget-box'>"
            "<b>Your budget:</b> "
            f"<input type='number' id='car-budget-input' min='{config.CAR_MIN_PRICE_EUR}' "
            f"max='{config.CAR_COMPARABLE_MAX_PRICE_EUR}' step='100' placeholder='e.g. 3500' "
            "style='width:110px'> "
            "<button type='button' id='car-budget-ok' "
            "class='primary'>OK</button> "
            "<button type='button' id='car-budget-reset' "
            "class='ghost'>Reset</button> "
            "<span class='note' id='car-budget-status'></span>"
            "<div style='margin-top:8px;font-size:14px'>"
            "<b>Filters:</b> "
            "<select id='car-filter-make'><option value=''>Any make</option></select> "
            "<input type='text' id='car-filter-model' placeholder='model' "
            "style='width:95px'> "
            "<select id='car-filter-fuel'><option value=''>Any fuel</option>"
            "<option value='petrol'>petrol</option><option value='diesel'>diesel</option>"
            "<option value='hybrid'>hybrid</option><option value='electric'>electric</option>"
            "<option value='lpg'>lpg</option></select> "
            "<select id='car-filter-gearbox'><option value=''>Any gearbox</option>"
            "<option value='manual'>manual</option>"
            "<option value='automatic'>automatic</option></select> "
            "<input type='number' id='car-filter-year' placeholder='min year' "
            f"min='{config.CAR_MIN_YEAR}' style='width:85px'> "
            "<input type='number' id='car-filter-km' placeholder='max km' "
            "min='0' step='10000' style='width:105px'> "
            "<input type='number' id='car-filter-min' placeholder='min €' "
            f"min='{config.CAR_MIN_PRICE_EUR}' step='500' style='width:80px'>"
            "</div>"
            "<p class='note' style='margin:6px 0 0'>Enter a maximum price "
            f"(from €{config.CAR_MIN_PRICE_EUR:,} up — the whole plausible "
            "market is embedded) "
            "and/or pick filters, then press <b>OK</b> (or Enter) to re-rank "
            "today's market snapshot — computed instantly in your browser "
            "from the data on this page, no rescraping (results also update "
            "as you type). Filters alone use the default "
            f"€{config.CAR_PRICE_CEILING_EUR:,} ceiling; they narrow "
            "<i>candidates</i> only — comparable pools always cover the whole "
            "market. <b>Reset</b> clears everything and returns to the "
            "default daily view. Badges and cross-source links appear in "
            "the default view only. <i>seen N d</i> = days since our scan "
            "first saw the ad (≈ days listed); €… → €… is the ask-price "
            "trail we have recorded. Shareable: append <b>?max=3500"
            "&amp;fuel=diesel&amp;km=200000</b> or <b>?model=passat</b> "
            "to this page's URL.</p>"
            "</div>"
            "<details class='box' id='car-watch-box'>"
            "<summary style='cursor:pointer'><b>★ Watchlist</b> "
            "<span class='note' id='car-watch-count'></span></summary>"
            "<div id='car-watch-list' style='margin-top:6px'></div>"
            "<p class='note' style='margin:6px 0 0'>Click ☆ on any deal to "
            "pin it here — stars are saved in this browser only "
            "(localStorage), never sent anywhere. A watched car missing "
            "from today's scan shows <b>no longer listed</b> — sold or the "
            "ad expired (the link may still open briefly). Price moves "
            "since you starred it show as ▼/▲.</p></details>")
    if market:
        top_html = (f"<div id='car-default-view'>{top_html}</div>"
                    "<div id='car-custom-view' style='display:none'></div>")

    gone_html = _gone_html(gone, seen, run_date)
    cuts_html = build_cuts_html(assessed, run_date)

    # KPI chips — qualifying / gone / health at a glance.
    kpi_bits = [web_style.kpi("qualifying", len(qualified))]
    n_new = sum(1 for b in badges.values() if b == "NEW")
    if n_new:
        kpi_bits.append(web_style.kpi("new", n_new, "good"))
    if gone:
        kpi_bits.append(web_style.kpi("gone", len(gone), "warn"))
    if source_errors:
        kpi_bits.append(web_style.kpi("errors", len(source_errors), "bad"))
    kpi_html = f"<div class='kpis'>{''.join(kpi_bits)}</div>"

    _STYLE = web_style.style_block()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">  <!-- no favicon file -> no 404 noise -->
<title>Riga car deals — {_e(run_date)}</title>
{_STYLE}
<script>
{web_style.SORT_JS}
</script>
{market_html}
{car_budget_script}
{car_watch_script}
</head><body>
{web_style.THEME_TOGGLE_HTML}
<h1>Riga car deals — {_e(stamp)}</h1>
<p class="note">Coverage: {coverage}. ss.com: the newest
{_e(config.CAR_SS_MAX_PAGES_PER_MAKE)} pages per make, then a bounded deep
scan of up to {_e(config.CAR_SS_MAX_MODELS)} model pages (newest
{_e(config.CAR_SS_MAX_PAGES_PER_MODEL)} pages each, models chosen by
observed ad volume — no model is favoured); pp.lv: newest
{_e(config.CAR_PP_MAX_PAGES)} pages. This is <b>not</b> an exhaustive scan
of either site.</p>
{kpi_html}
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
<b>Eligibility:</b> every seller ad asking
{_fmt_eur(config.CAR_MIN_PRICE_EUR)} or more
with year ≥{_e(config.CAR_MIN_YEAR)} and mileage
≤{config.CAR_MAX_MILEAGE_KM:,} km, identified fuel (and engine where
applicable), is embedded and comparable; candidates are capped at the provisional
{_fmt_eur(config.CAR_PRICE_CEILING_EUR)} ceiling in the default view —
raise it with the budget box.
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
<p class="secnav">Jump to:
<a href="#sec-deals">Deals</a><a href="#sec-cuts">Price cuts</a><a
href="#sec-gone">Gone</a></p>
<div id="sec-deals">{top_html}</div>
<div id="sec-cuts">{cuts_html}</div>
<div id="sec-gone">{gone_html}</div>
<div class="box">
<b>Before buying:</b> check mileage and history in the CSDD register
(e.csdd.lv), get an independent mechanical inspection, and verify all
documentation (registration, service records, outstanding finance).
</div>
<hr><p class="note">Generated by Flat_Searcher car digest.</p>
</body></html>"""
