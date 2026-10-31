"""Shared CSS design system for all generated HTML pages.

One source of truth: notifier.py (flat digest), car_digest.py (car
digest) and website.py (index/cars/market/archive pages) all inject
BASE_CSS so the whole site shares one modern look. Theme colors are
CSS custom properties so `prefers-color-scheme: dark` swaps the whole
palette without touching markup — inline styles in generated HTML
reference var(--token) for colors.

Rule 1/2: everything hard-coded, constants at the top.
"""

import html

# ── Design tokens ─────────────────────────────────────────────────
# Light palette (slate + blue accent, Tailwind-inspired).
L_BG        = "#f1f5f9"   # page background
L_CARD      = "#ffffff"   # cards / boxes
L_FG        = "#0f172a"   # primary text
L_MUTED     = "#5d6b7d"   # secondary text
L_FAINT     = "#93a1b3"   # tertiary text / icons
L_LINE      = "#e2e8f0"   # hairline borders
L_LINE2     = "#cbd5e1"   # stronger borders
L_THBG      = "#f8fafc"   # table header background
L_ROWALT    = "#f8fafc"   # zebra row
L_HOVER     = "#eef4fb"   # row hover
L_LINK      = "#2563eb"   # links
L_ACCENT    = "#1d4ed8"   # headings / brand
L_GOOD      = "#16a34a"   # price drops, ok
L_GOODBG    = "#e8f7ee"
L_BAD       = "#dc2626"   # warnings, price rises, ended
L_BADBG     = "#fdeaea"
L_WARN      = "#b45309"   # amber text
L_WARNBG    = "#fdf4e3"
L_WARNLINE  = "#ecd9b0"
L_INFOBG    = "#f4f8fc"   # neutral info boxes
L_INFOLINE  = "#d4e0ec"
L_AUCTION   = "#7c3aed"   # izsoles auctions
L_STAR      = "#d4a017"   # watchlist stars
L_BADGEBG   = "#eef2f7"   # neutral badge pill

# Dark palette.
D_BG        = "#0d1420"
D_CARD      = "#17202e"
D_FG        = "#e2e8f0"
D_MUTED     = "#94a3b8"
D_FAINT     = "#64748b"
D_LINE      = "#2c3a4f"
D_LINE2     = "#3b4c63"
D_THBG      = "#1b2637"
D_ROWALT    = "#16202f"
D_HOVER     = "#22304a"
D_LINK      = "#6ea8fe"
D_ACCENT    = "#93b4fd"
D_GOOD      = "#4ade80"
D_GOODBG    = "#14532d"
D_BAD       = "#f87171"
D_BADBG     = "#442222"
D_WARN      = "#fbbf24"
D_WARNBG    = "#3a2c12"
D_WARNLINE  = "#57441d"
D_INFOBG    = "#16233a"
D_INFOLINE  = "#2b3f5c"
D_AUCTION   = "#a78bfa"
D_STAR      = "#facc15"
D_BADGEBG   = "#26344a"

# ── Base stylesheet ───────────────────────────────────────────────
# Written inside an f-string in callers, so literal braces are doubled
# here only where the CSS would otherwise be misread — this module
# uses a plain string (no f-string), so braces stay single and callers
# must insert it into f-strings via a variable, not inline text.
BASE_CSS = """
:root{
  --bg:L_BG; --card:L_CARD; --fg:L_FG; --muted:L_MUTED; --faint:L_FAINT;
  --line:L_LINE; --line2:L_LINE2; --th-bg:L_THBG; --row-alt:L_ROWALT;
  --hover:L_HOVER; --link:L_LINK; --accent:L_ACCENT;
  --good:L_GOOD; --good-bg:L_GOODBG; --bad:L_BAD; --bad-bg:L_BADBG;
  --warn:L_WARN; --warn-bg:L_WARNBG; --warn-line:L_WARNLINE;
  --info-bg:L_INFOBG; --info-line:L_INFOLINE;
  --auction:L_AUCTION; --star:L_STAR; --badge-bg:L_BADGEBG;
  --radius:10px; --radius-s:6px;
  --shadow:0 1px 2px rgba(15,23,42,.06),0 1px 3px rgba(15,23,42,.08);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:D_BG; --card:D_CARD; --fg:D_FG; --muted:D_MUTED; --faint:D_FAINT;
    --line:D_LINE; --line2:D_LINE2; --th-bg:D_THBG; --row-alt:D_ROWALT;
    --hover:D_HOVER; --link:D_LINK; --accent:D_ACCENT;
    --good:D_GOOD; --good-bg:D_GOODBG; --bad:D_BAD; --bad-bg:D_BADBG;
    --warn:D_WARN; --warn-bg:D_WARNBG; --warn-line:D_WARNLINE;
    --info-bg:D_INFOBG; --info-line:D_INFOLINE;
    --auction:D_AUCTION; --star:D_STAR; --badge-bg:D_BADGEBG;
    --shadow:0 1px 2px rgba(0,0,0,.35);
  }
}
/* Manual override: a data-theme attr on <html> beats the OS preference
   (specificity :root[attr] > :root inside the media query). The light
   override must re-declare the tokens or a dark-OS user could never
   switch back. */
:root[data-theme="dark"]{
  --bg:D_BG; --card:D_CARD; --fg:D_FG; --muted:D_MUTED; --faint:D_FAINT;
  --line:D_LINE; --line2:D_LINE2; --th-bg:D_THBG; --row-alt:D_ROWALT;
  --hover:D_HOVER; --link:D_LINK; --accent:D_ACCENT;
  --good:D_GOOD; --good-bg:D_GOODBG; --bad:D_BAD; --bad-bg:D_BADBG;
  --warn:D_WARN; --warn-bg:D_WARNBG; --warn-line:D_WARNLINE;
  --info-bg:D_INFOBG; --info-line:D_INFOLINE;
  --auction:D_AUCTION; --star:D_STAR; --badge-bg:D_BADGEBG;
  --shadow:0 1px 2px rgba(0,0,0,.35);
}
:root[data-theme="light"]{
  --bg:L_BG; --card:L_CARD; --fg:L_FG; --muted:L_MUTED; --faint:L_FAINT;
  --line:L_LINE; --line2:L_LINE2; --th-bg:L_THBG; --row-alt:L_ROWALT;
  --hover:L_HOVER; --link:L_LINK; --accent:L_ACCENT;
  --good:L_GOOD; --good-bg:L_GOODBG; --bad:L_BAD; --bad-bg:L_BADBG;
  --warn:L_WARN; --warn-bg:L_WARNBG; --warn-line:L_WARNLINE;
  --info-bg:L_INFOBG; --info-line:L_INFOLINE;
  --auction:L_AUCTION; --star:L_STAR; --badge-bg:L_BADGEBG;
  --radius:10px; --radius-s:6px;
  --shadow:0 1px 2px rgba(15,23,42,.06),0 1px 3px rgba(15,23,42,.08);
}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  background:var(--bg);color:var(--fg);max-width:1020px;margin:0 auto;
  padding:0 18px 40px;line-height:1.45;font-size:15px}
h1,h2{color:var(--accent);font-weight:700;letter-spacing:-.01em}
h2{font-size:22px;margin:8px 0 4px}
h3{font-size:16px;color:var(--fg);border-bottom:2px solid var(--line);
  padding-bottom:6px;margin:26px 0 10px}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
p{margin:6px 0}
hr{border:0;border-top:1px solid var(--line);margin:28px 0 14px}
.note{color:var(--muted);font-size:12.5px}
table{border-collapse:collapse;width:100%;font-size:13.5px;
  margin:4px 0 10px}
th{position:sticky;top:0;z-index:1;background:var(--th-bg);color:var(--muted);
  font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  text-align:left;padding:7px 10px;border-bottom:2px solid var(--line2);
  user-select:none;cursor:default;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;
  font-variant-numeric:tabular-nums}
tr:hover>td{background:var(--hover)}
tr.z>td{background:var(--row-alt)}
tr.z:hover>td{background:var(--hover)}
tr:target>td{background:var(--warn-bg)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
th.sort-th{cursor:pointer}
th.sort-th:hover{background:var(--hover);color:var(--fg)}
th.sort-th::after{content:"\\21C5";font-size:10px;color:var(--faint);margin-left:4px;opacity:0}
th.sort-th:hover::after{opacity:1}
th.sort-asc::after{content:"\\2191";font-size:10px;color:var(--accent);margin-left:4px;opacity:1}
th.sort-desc::after{content:"\\2193";font-size:10px;color:var(--accent);margin-left:4px;opacity:1}
.scroll-x{overflow-x:auto;-webkit-overflow-scrolling:touch}

/* Cards & status boxes */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px 16px;margin:14px 0;box-shadow:var(--shadow)}
.card h3{margin-top:0}
.card table{margin-bottom:0}
.box,.info{background:var(--info-bg);border:1px solid var(--info-line);
  border-radius:var(--radius-s);padding:10px 14px;margin:12px 0}
.warn,.stale-warning{background:var(--bad-bg);border:1px solid var(--bad);
  border-radius:var(--radius-s);padding:10px 14px;margin:12px 0}
.amber{background:var(--warn-bg);border:1px solid var(--warn-line);
  border-radius:var(--radius-s);padding:10px 14px;margin:12px 0}
.card.amber{background:var(--warn-bg);border-color:var(--warn-line)}
.card.err{background:var(--bad-bg);border-color:var(--bad)}

/* Form controls (budget/filter inputs) */
input,select,button{font:inherit;font-size:13px;color:var(--fg);
  background:var(--card);border:1px solid var(--line2);
  border-radius:var(--radius-s);padding:5px 8px}
input:focus,select:focus{outline:2px solid var(--link);outline-offset:0;
  border-color:var(--link)}
button{cursor:pointer}
button.primary{background:var(--accent);color:#fff;
  border-color:transparent;font-weight:700}
button.ghost{background:var(--row-alt);font-weight:700}

/* KPI stat chips */
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:8px 14px;box-shadow:var(--shadow);min-width:86px}
.kpi b{display:block;font-size:17px;color:var(--fg);font-variant-numeric:tabular-nums}
.kpi span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.kpi.warn b{color:var(--warn)} .kpi.bad b{color:var(--bad)} .kpi.good b{color:var(--good)}

/* Badges — rounded pills */
.badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10.5px;
  font-weight:600;line-height:1.5;white-space:nowrap;vertical-align:1px;
  background:var(--badge-bg);color:var(--muted)}
.badge.b-new{background:var(--good-bg);color:var(--good)}
.badge.b-up{background:var(--bad-bg);color:var(--bad)}
.badge.b-down{background:var(--good-bg);color:var(--good)}
.badge.b-end{background:var(--bad-bg);color:var(--bad)}
.badge.b-ended{background:var(--badge-bg);color:var(--muted)}
.badge.b-reg{background:var(--warn-bg);color:var(--warn)}
.badge.b-auction{background:transparent;border:1px solid var(--auction);color:var(--auction)}
.badge.b-src{border:1px solid var(--line2);color:var(--muted);background:transparent}
.badge.b-cheap{background:var(--good-bg);color:var(--good)}
.badge.b-mot{background:var(--warn-bg);color:var(--warn)}
.badge.b-relist{background:transparent;border:1px solid var(--auction);color:var(--auction)}
.badge.b-low{background:var(--good-bg);color:var(--good)}
/* Gearbox circle marker — M/A/? on car spec lines */
.gbadge{display:inline-flex;align-items:center;justify-content:center;
  width:15px;height:15px;border-radius:50%;border:1.5px solid var(--accent);
  color:var(--accent);font-size:9.5px;font-weight:700;line-height:1;
  vertical-align:-2px;margin-right:4px}
.gbadge.gb-a{border-color:var(--good);color:var(--good)}
.gbadge.gb-unknown{border-color:var(--faint);color:var(--faint)}
/* In-page section jump nav (chip row above the digest sections) */
.secnav{margin:4px 0 14px;font-size:12px;color:var(--faint)}
.secnav a{display:inline-block;padding:2px 9px;margin:2px 3px 2px 0;
  border:1px solid var(--line2);border-radius:999px;color:var(--link);
  text-decoration:none;background:var(--card)}
.secnav a:hover{border-color:var(--accent);color:var(--accent)}
/* Dark-mode manual toggle — fixed corner button */
.theme-toggle{position:fixed;top:10px;right:12px;z-index:60;width:34px;
  height:34px;border-radius:50%;border:1px solid var(--line2);
  background:var(--card);color:var(--fg);font-size:16px;line-height:1;
  cursor:pointer;box-shadow:var(--shadow);padding:0}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent)}
/* Back-to-top — appears after scrolling, bottom-right corner */
.top-btn{position:fixed;bottom:14px;right:12px;z-index:60;width:34px;
  height:34px;border-radius:50%;border:1px solid var(--line2);
  background:var(--card);color:var(--fg);font-size:15px;line-height:1;
  cursor:pointer;box-shadow:var(--shadow);padding:0;display:none}
.top-btn:hover{border-color:var(--accent);color:var(--accent)}
/* Leaflet tiles follow dark mode (OSM has no dark tile set — the
   standard invert+hue-rotate trick keeps roads/labels readable) */
[data-theme="dark"] .leaflet-tile{filter:invert(1) hue-rotate(180deg)
  brightness(.9) saturate(.7)}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .leaflet-tile{
    filter:invert(1) hue-rotate(180deg) brightness(.9) saturate(.7)}}
/* Print: content only — nav, toggles, buttons and controls go away */
@media print{
  .site-nav,.secnav,.theme-toggle,.top-btn,.watch-star,.watch-remove,
  select,input,button{display:none!important}
  .card,.box{box-shadow:none;border:1px solid #ccc}
  a{color:#000;text-decoration:none}}
.delta-up{color:var(--bad)} .delta-down{color:var(--good)}

/* Watchlist star */
.watch-star{cursor:pointer;border:0;background:none;font-size:15px;color:var(--star);
  padding:0 2px;opacity:.75}
.watch-star:hover{opacity:1;transform:scale(1.15)}
.watch-star.on{opacity:1}

/* Site nav pills (website.py _nav_html) */
.site-nav{margin:0 0 18px}
.site-nav a{display:inline-block;padding:7px 16px;margin-right:6px;border-radius:999px;
  text-decoration:none;font-size:13.5px;font-weight:600;
  background:var(--card);color:var(--accent);border:1px solid var(--line)}
.site-nav a:hover{background:var(--hover);text-decoration:none}
.site-nav a.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.site-nav a.on:hover{filter:brightness(1.08)}
.daynav{font-size:12px;margin:2px 0 12px;color:var(--faint)}
.daynav a{color:var(--link);text-decoration:none;padding:1px 6px}
.daynav a:hover{text-decoration:underline}
.daynav .cur{font-weight:600;color:var(--fg);padding:0 6px}
.daynav .off{color:var(--faint);padding:1px 6px}

/* Archive coverage strip cells */
.cov-cell{display:inline-block;width:14px;height:14px;margin-right:3px;
  border-radius:3px;vertical-align:middle}
.cov-both{background:var(--good)} .cov-one{background:var(--warn)}
.cov-none{background:var(--bad)}

/* Details/summary */
details{border-radius:var(--radius-s)}
details>summary{cursor:pointer;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"\\25B8";display:inline-block;margin-right:6px;
  color:var(--faint);transition:transform .15s}
details[open]>summary::before{transform:rotate(90deg)}

/* Flat map (Leaflet) */
#map-container{margin:20px 0;border:1px solid var(--line);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow)}
#map-container .map-header{padding:10px 14px;background:var(--accent);color:#fff;
  font-size:13px;font-weight:600;letter-spacing:.02em}
#map-container #map{width:100%;height:400px}
.school-label{background:none;border:none;color:var(--bad);font-weight:bold;
  font-size:12px;text-shadow:0 1px 2px #fff,0 -1px 2px #fff,1px 0 2px #fff,-1px 0 2px #fff}

/* Price-timeline subrows */
.timeline-row td{border-top:none;background:var(--row-alt);font-size:11.5px;
  line-height:1.6;color:var(--muted);padding:6px 10px}

/* Accessibility & small screens */
a:focus-visible,.watch-star:focus-visible,th.sort-th:focus-visible,
summary:focus-visible{outline:2px solid var(--link);outline-offset:2px;border-radius:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media (max-width:720px){
  body{padding:0 10px 30px;font-size:14px}
  th,td{padding:6px 7px}
  .kpi{min-width:70px;padding:6px 10px}
}
"""

# Standalone nav styles — emitted by website._nav_html itself so injected
# nav still looks right on legacy archived pages that predate BASE_CSS
# (var() fallbacks resolve to the light palette there).
NAV_CSS = (
    ".site-nav{margin:0 0 18px}"
    ".site-nav a{display:inline-block;padding:7px 16px;margin-right:6px;"
    "border-radius:999px;text-decoration:none;font-size:13.5px;font-weight:600;"
    "background:var(--card,#fff);color:var(--accent,#1d4ed8);"
    "border:1px solid var(--line,#e2e8f0)}"
    ".site-nav a:hover{background:var(--hover,#eef4fb);text-decoration:none}"
    ".site-nav a.on{background:var(--accent,#1d4ed8);color:#fff;"
    "border-color:var(--accent,#1d4ed8)}"
    # prev/next day bar injected by website._refresh_daynav
    ".daynav{font-size:12px;margin:2px 0 12px;color:var(--faint,#94a3b8)}"
    ".daynav a{color:var(--link,#2563eb);text-decoration:none;padding:1px 6px}"
    ".daynav a:hover{text-decoration:underline}"
    ".daynav .cur{font-weight:600;color:var(--fg,#0f172a);padding:0 6px}"
    ".daynav .off{color:var(--faint,#94a3b8);padding:1px 6px}"
)


# Applies a saved theme choice BEFORE first paint (no flash); rides inside
# style_block() so every generated page gets it for free.
THEME_HEAD_JS = """<script>(function(){try{var t=localStorage.getItem('fs_theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>"""

# The fixed corner button + its handler. Reads OS preference as the
# default when nothing was saved; persists to localStorage fs_theme.
THEME_TOGGLE_HTML = """<button type="button" id="theme-toggle" class="theme-toggle"
title="Toggle dark/light (your choice is remembered)">&#9790;</button>
<script>(function(){var b=document.getElementById('theme-toggle');var de=document.documentElement;
function cur(){return de.getAttribute('data-theme')||(window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');}
function paint(){b.innerHTML=cur()==='dark'?'&#9788;':'&#9790;';}
b.onclick=function(){var next=cur()==='dark'?'light':'dark';de.setAttribute('data-theme',next);try{localStorage.setItem('fs_theme',next);}catch(e){}paint();};
paint();})();</script>"""


# Back-to-top — floating button that appears once the page is scrolled,
# plus global '/' shortcut to focus the page's primary filter/budget box.
TOP_BTN_HTML = """<button type="button" id="top-btn" class="top-btn"
title="Back to top">&#8593;</button>
<script>(function(){var b=document.getElementById('top-btn');
function paint(){b.style.display=(window.scrollY>600)?'block':'none';}
b.onclick=function(){window.scrollTo({top:0,behavior:'smooth'});};
if(window.addEventListener){addEventListener('scroll',paint,{passive:true});
addEventListener('keydown',function(e){
var a=document.activeElement,tag=a&&a.tagName?a.tagName.toUpperCase():'';
if(e.key==='Escape'&&(tag==='INPUT'||tag==='SELECT')){a.blur();return;}
if(e.key==='/'&&tag!=='INPUT'&&tag!=='TEXTAREA'&&tag!=='SELECT'&&!e.ctrlKey&&!e.metaKey){
var el=document.querySelector&&document.querySelector('input[type="number"],input[type="text"]');
if(el){e.preventDefault();el.focus();if(el.select)el.select();}}});}
paint();})();</script>"""


# Generic "filter rows by text" widget for multi-table pages (market page).
# Hides <tr> whose text doesn't contain the query; header rows (with <th>)
# always stay. The input's id is row-filter; the script is idempotent.
ROW_FILTER_HTML = """<input type="text" id="row-filter"
placeholder="filter rows&hellip;" style="width:140px;margin-left:8px"
title="Show only rows containing this text">
<script>(function(){
var q=document.getElementById('row-filter');if(!q)return;
q.addEventListener('input',function(){
var needle=q.value.toLowerCase();
var trs=document.querySelectorAll('table tr');
for(var i=0;i<trs.length;i++){var tr=trs[i];
if(tr.querySelector('th'))continue;
tr.style.display=(!needle||tr.textContent.toLowerCase()
.indexOf(needle)>=0)?'':'none';}});
})();</script>"""


def style_block(extra=""):
    """`<style>` element holding BASE_CSS (+ optional page CSS)."""
    css = BASE_CSS
    # Fill token placeholders (kept as bare names so BASE_CSS stays a
    # plain string and safe to embed inside callers' f-strings).
    tokens = {
        "L_BG": L_BG, "L_CARD": L_CARD, "L_FG": L_FG, "L_MUTED": L_MUTED,
        "L_FAINT": L_FAINT, "L_LINE": L_LINE, "L_LINE2": L_LINE2,
        "L_THBG": L_THBG, "L_ROWALT": L_ROWALT, "L_HOVER": L_HOVER,
        "L_LINK": L_LINK, "L_ACCENT": L_ACCENT, "L_GOOD": L_GOOD,
        "L_GOODBG": L_GOODBG, "L_BAD": L_BAD, "L_BADBG": L_BADBG,
        "L_WARN": L_WARN, "L_WARNBG": L_WARNBG, "L_WARNLINE": L_WARNLINE,
        "L_INFOBG": L_INFOBG, "L_INFOLINE": L_INFOLINE,
        "L_AUCTION": L_AUCTION, "L_STAR": L_STAR, "L_BADGEBG": L_BADGEBG,
        "D_BG": D_BG, "D_CARD": D_CARD, "D_FG": D_FG, "D_MUTED": D_MUTED,
        "D_FAINT": D_FAINT, "D_LINE": D_LINE, "D_LINE2": D_LINE2,
        "D_THBG": D_THBG, "D_ROWALT": D_ROWALT, "D_HOVER": D_HOVER,
        "D_LINK": D_LINK, "D_ACCENT": D_ACCENT, "D_GOOD": D_GOOD,
        "D_GOODBG": D_GOODBG, "D_BAD": D_BAD, "D_BADBG": D_BADBG,
        "D_WARN": D_WARN, "D_WARNBG": D_WARNBG, "D_WARNLINE": D_WARNLINE,
        "D_INFOBG": D_INFOBG, "D_INFOLINE": D_INFOLINE,
        "D_AUCTION": D_AUCTION, "D_STAR": D_STAR, "D_BADGEBG": D_BADGEBG,
    }
    # Longest first — L_LINE2 must fill before L_LINE, L_WARNLINE before
    # L_WARN, etc., or the shorter prefix eats the longer token name.
    for k in sorted(tokens, key=len, reverse=True):
        css = css.replace(k, tokens[k])
    return f"<style>{css}{extra}</style>{THEME_HEAD_JS}"


def kpi(label, value, cls="", title=""):
    """One dashboard stat chip; `title` adds a hover tooltip."""
    c = f"kpi {cls}".strip()
    t = f" title='{html.escape(str(title))}'" if title else ""
    return f"<div class='{c}'{t}><b>{value}</b><span>{label}</span></div>"


def badge(text, cls):
    """Rounded status pill."""
    return f"<span class='badge {cls}'>{text}</span>"


def motivated_badge():
    """The amber MOTIVATED pill used by the price-cuts cards (hover
    explains the signal)."""
    return (" <span class='badge b-mot' "
            "title='Stale listing + real cut: seller may be "
            "negotiable'>MOTIVATED</span>")


def histogram_card_html(prices, bucket_eur, title, note, unit_label="ads",
                        h=90, bh=60):
    """Shared SVG ask-price histogram card used by the flat and car digests."""
    if len(prices) < 10:
        return ""
    hi = int(prices[-1] // bucket_eur) + 1
    lo = int(prices[0] // bucket_eur)
    buckets = [0] * max(1, hi - lo)
    for p in prices:
        buckets[int(p // bucket_eur) - lo] += 1
    peak = max(buckets) or 1
    w = 640
    bw = w / len(buckets)
    bars = []
    for i, n in enumerate(buckets):
        if not n:
            continue
        bh_i = max(2, int(n / peak * bh))
        bars.append(
            f"<rect x='{i*bw:.1f}' y='{bh-bh_i}' width='{max(1.0, bw-1):.1f}' "
            f"height='{bh_i}' style='fill:var(--accent)' opacity='0.75'>"
            f"<title>€{int((lo+i)*bucket_eur):,}–"
            f"€{int((lo+i+1)*bucket_eur):,}: {n} {unit_label}</title></rect>")
    svg = (f"<svg viewBox='0 0 {w} {h}' style='width:100%;max-width:640px;"
           f"height:auto;display:block' preserveAspectRatio='none'>"
           f"{''.join(bars)}"
           f"<text x='0' y='{h-2}' font-size='9' style='fill:var(--faint)'>"
           f"€{lo*bucket_eur:,}</text>"
           f"<text x='{w}' y='{h-2}' font-size='9' text-anchor='end' "
           f"style='fill:var(--faint)'>€{hi*bucket_eur:,}</text></svg>")
    return (f"<div class='card'>"
            f"<h3 style='margin:0 0 4px 0'>{title}</h3>"
            f"<p class='note' style='margin:0 0 6px 0'>{note}</p>"
            f"{svg}</div>")


# Shared inline-SVG price-trail sparkline builder for the browser budget
# tools (interpolated into both FLAT_BUDGET_JS and CAR_BUDGET_JS so each
# script stays self-contained for Node eval tests).
SPARK_EL_JS = """  function sparkEl(hist) {
    if (typeof document.createElementNS === 'undefined') return null;
    var pts = [];
    (hist || []).forEach(function (h) {
      var v = Number(h[1]);
      if (!isNaN(v)) pts.push(v);
    });
    if (pts.length < 2) return null;
    var w = 64, h = 16;
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    var span = hi - lo;
    var coords = pts.map(function (v, i) {
      var y = span === 0 ? (h / 2) : (h - 3 - (v - lo) / span * (h - 6));
      return (i * (w - 4) / (pts.length - 1) + 2).toFixed(1) + ',' + y.toFixed(1);
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
  }"""


# Click-to-sort table headers, shared by both digests.
SORT_JS = r"""// Click-to-sort table headers. Timeline rows stay attached to their
// parent row (the flat digest interleaves them; tables without
// .timeline-row elements are unaffected).
var sortState = {};
function sortTable(tableId, colIdx) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var ths = table.querySelectorAll('th.sort-th');
  ths.forEach(function(th) { th.classList.remove('sort-asc','sort-desc'); });
  var rows = Array.from(table.querySelectorAll('tr')).slice(1);
  var groups = [];
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].classList.contains('timeline-row')) {
      if (groups.length) groups[groups.length-1].push(rows[i]);
    } else {
      groups.push([rows[i]]);
    }
  }
  var key = tableId + '_' + colIdx;
  sortState[key] = !sortState[key];
  var asc = sortState[key];
  var clickedTh = table.querySelectorAll('th')[colIdx];
  if (clickedTh) clickedTh.classList.add(asc ? 'sort-asc' : 'sort-desc');
  groups.sort(function(a, b) {
    var ca = a[0].children && a[0].children[colIdx];
    var cb = b[0].children && b[0].children[colIdx];
    if (!ca || !cb) return 0;
    var va = ca.getAttribute('data-sort');
    var vb = cb.getAttribute('data-sort');
    if (va === null) va = ca.textContent || '';
    if (vb === null) vb = cb.textContent || '';
    va = String(va).trim(); vb = String(vb).trim();
    var na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb) && /^-?\d+(\.\d+)?$/.test(va) && /^-?\d+(\.\d+)?$/.test(vb)) {
      return asc ? na - nb : nb - na;
    }
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  for (var g = 0; g < groups.length; g++) {
    for (var r = 0; r < groups[g].length; r++) {
      table.appendChild(groups[g][r]);
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Shared ☆ watchlist script — one template parameterised per page.
# Stars persist picks in localStorage; a watched listing missing from
# today's embedded data is flagged "no longer listed" (sold/expired).
# @NS@ = id/global prefix ("flat"/"car"), @DATA_ID@ = embedded-JSON element
# id, @STORAGE_KEY@ = localStorage key.
# ---------------------------------------------------------------------------
_WATCH_JS_TEMPLATE = r"""
function __@NS@WatchInit() {
  var dataEl = document.getElementById('@DATA_ID@');
  var box = document.getElementById('@NS@-watch-box');
  var listEl = document.getElementById('@NS@-watch-list');
  var countEl = document.getElementById('@NS@-watch-count');
  if (!dataEl || !box || !listEl) return;
  if (typeof localStorage === 'undefined') return;
  var payload = JSON.parse(dataEl.textContent);
  var F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      (payload.rows || []).concat(payload.extra || []).forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  var byKey = {};
  payload.rows.forEach(function (r) {
    if (r[idx.source] != null && r[idx.id] != null)
      byKey[r[idx.source] + ':' + r[idx.id]] = r;
  });
  (payload.extra || []).forEach(function (r) {
    if (r[idx.source] != null && r[idx.id] != null) {
      var k = r[idx.source] + ':' + r[idx.id];
      if (!byKey[k]) byKey[k] = r;
    }
  });
  var KEY = '@STORAGE_KEY@';
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

  function clearGone() {
    var w = load();
    Object.keys(w).forEach(function (k) {
      if (!byKey[k]) delete w[k];
    });
    save(w);
    refreshStars();
    renderBox();
  }

  function renderBox() {
    var w = load();
    var keys = Object.keys(w).sort(function (a, b) {
      return String(w[b].added || '').localeCompare(String(w[a].added || ''));
    });
    var nDropped = 0, nGone = 0;
    keys.forEach(function (k) {
      var cur = byKey[k];
      if (!cur) { nGone++; return; }
      var p0 = Number(w[k].price), p1 = Number(cur[idx.price_eur]);
      if (isFinite(p0) && isFinite(p1) && p1 < p0 - 0.5) nDropped++;
    });
    if (countEl) {
      countEl.textContent = '(' + keys.length + ')';
      var tips = [];
      if (nDropped) tips.push(nDropped + ' price drop(s) since starred');
      if (nGone) tips.push(nGone + ' no longer listed');
      countEl.title = tips.join(' · ');
    }
    listEl.innerHTML = '';
    if (!keys.length) {
      var p = document.createElement('p');
      p.className = 'note';
      p.textContent = 'Nothing starred yet — click ☆ on a listing to pin it here.';
      listEl.appendChild(p);
      return;
    }
    if (nDropped || nGone) {
      var bar = document.createElement('div');
      bar.className = 'note';
      bar.style.marginBottom = '6px';
      var sbits = [];
      if (nDropped) sbits.push(nDropped + ' dropped since starred');
      if (nGone) sbits.push(nGone + ' no longer listed');
      bar.textContent = sbits.join(' · ') + ' ';
      if (nGone) {
        var cg = document.createElement('button');
        cg.type = 'button';
        cg.className = 'ghost watch-clear-gone';
        cg.style.cssText = 'font-size:11px;padding:2px 8px;margin-left:6px';
        cg.textContent = 'Clear gone (' + nGone + ')';
        bar.appendChild(cg);
      }
      listEl.appendChild(bar);
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
    } else if (t.classList.contains('watch-clear-gone')) {
      clearGone();
    }
  });
  refreshStars();
  renderBox();
  if (typeof window !== 'undefined') {
    window.__@NS@Watch = { toggle: toggle, renderBox: renderBox, load: load,
                           clearGone: clearGone };
    window.__@NS@WatchRefresh = refreshStars;
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __@NS@WatchInit);
} else {
  __@NS@WatchInit();
}
"""


def watch_js(ns, data_id, storage_key):
    """Instantiate the shared watchlist script for a page.
    ns='flat'/'car' (dom-id + window-global prefix), data_id is the
    embedded-JSON element id, storage_key the localStorage key."""
    return (_WATCH_JS_TEMPLATE
            .replace("@NS@", ns)
            .replace("@DATA_ID@", data_id)
            .replace("@STORAGE_KEY@", storage_key))


# In-browser budget filter for the flats digest: shows ALL scored listings
# within a custom budget (the daily tables cap at TOP_N_PER_TYPE), ranked
# by deal score. Filtering only — the regression score does not depend on
# the buyer's budget. Reuses the page's sortTable().
FLAT_BUDGET_JS = """
// Runs after the DOM is ready: the budget input lives in the body
// element, below this script in the head, so it does not exist at parse
// time. (No literal '<body>' here — website._inject_nav searches for it.)
function __flatBudgetInit() {
  var dataEl = document.getElementById('flat-listings-data');
  var input = document.getElementById('flat-budget-input');
  var minInput = document.getElementById('flat-budget-min');
  var statusEl = document.getElementById('flat-budget-status');
  var customView = document.getElementById('flat-custom-view');
  if (!dataEl || !input || !customView) return;
  var payload = JSON.parse(dataEl.textContent);
  var cfg = payload.config, F = payload.fields, idx = {};
  F.forEach(function (f, i) { idx[f] = i; });
  // Dictionary-encoded string columns -> real strings, before any reads.
  if (payload.dict) {
    Object.keys(payload.dict).forEach(function (f) {
      var i = idx[f], dict = payload.dict[f];
      (payload.rows || []).concat(payload.extra || []).forEach(function (r) {
        if (r[i] != null) r[i] = dict[r[i]];
      });
    });
  }
  // Budget search covers the WHOLE embedded market: scored rows plus the
  // unscored extras (near-school/overflow listings) — every flat kept by
  // today's sanity floor, regardless of price.
  var rows = payload.rows.concat(payload.extra || []);
  var districtSel = document.getElementById('flat-filter-district');
  var roomsSel = document.getElementById('flat-filter-rooms');
  var areaInput = document.getElementById('flat-filter-area');
  var kmInput = document.getElementById('flat-filter-km');
  var midFloorChk = document.getElementById('flat-filter-midfloor');

  // Fill the district dropdown from today's data.
  if (districtSel) {
    var seen = {};
    rows.forEach(function (r) {
      if (r[idx.district] && (r[idx.deal_type] || 'sale') === 'sale')
        seen[r[idx.district]] = 1;
    });
    Object.keys(seen).sort().forEach(function (d) {
      var o = document.createElement('option');
      o.value = d; o.textContent = d;
      districtSel.appendChild(o);
    });
  }

  function readFilters() {
    return {
      district: districtSel && districtSel.value ? districtSel.value : '',
      rooms: roomsSel && roomsSel.value ? roomsSel.value : '',
      minArea: areaInput ? (parseFloat(areaInput.value) || 0) : 0,
      maxKm: kmInput ? (parseFloat(kmInput.value) || 0) : 0,
      midFloor: !!(midFloorChk && midFloorChk.checked)
    };
  }
  function anyFilterSet(f) {
    return !!(f.district || f.rooms || f.minArea || f.maxKm || f.midFloor);
  }
  function passesFilters(r, f) {
    if (f.district && r[idx.district] !== f.district) return false;
    if (f.rooms === '5') {
      if (!(r[idx.rooms] >= 5)) return false;   // '5+' means five or more
    } else if (f.rooms && String(r[idx.rooms]) !== f.rooms) return false;
    if (f.minArea && (r[idx.area_m2] == null || r[idx.area_m2] < f.minArea)) return false;
    if (f.maxKm && (r[idx.school_km] == null || r[idx.school_km] > f.maxKm)) return false;
    if (f.midFloor) {
      var fl = String(r[idx.floor] || '');
      var fm = fl.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
      if (!fm) return false;
      var fn = parseInt(fm[1], 10), ft = parseInt(fm[2], 10);
      if (fn <= 1 || (ft > 1 && fn >= ft)) return false;
    }
    // Rent/auction entries are excluded from the regular sale budget view.
    // A null field means the pipeline didn't tag it -> treat as sale.
    if ((r[idx.deal_type] || 'sale') !== 'sale') return false;
    return true;
  }

  function fmtEur(v) {
    return v == null ? '—' : '€' + Math.round(v).toLocaleString('en-US');
  }

""" + SPARK_EL_JS + """
  var refNowMs = Math.max(Date.now(),
    (cfg && cfg.runDate) ? (Date.parse(String(cfg.runDate) + 'T00:00:00Z') || 0) : 0);

  function cell(text, sortVal, alignRight) {
    var td = document.createElement('td');
    td.style.padding = '5px';
    if (alignRight) td.style.textAlign = 'right';
    if (sortVal !== undefined && sortVal !== null) {
      td.setAttribute('data-sort', sortVal);
    }
    td.textContent = text;
    return td;
  }

  function render(matches, maxPrice) {
    customView.innerHTML = '';
    var h3 = document.createElement('h3');
    h3.textContent = 'Within your €' + maxPrice.toLocaleString('en-US') +
                     ' budget — ' + matches.length + ' listing(s), ranked by deal score';
    customView.appendChild(h3);
    var note = document.createElement('p');
    note.className = 'note';
    note.textContent = 'All of today\\'s scored listings within this budget ' +
      '(the daily sections below show the newest/top-N view). ' +
      'Click column headers to sort.';
    customView.appendChild(note);
    if (!matches.length) {
      var p = document.createElement('p');
      p.textContent = 'No listings within this budget today.';
      customView.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    table.id = 'flat-custom';
    var headers = ['District', 'Street', 'Distance', 'Rooms', 'm²', 'Floor',
                   'Price', 'EUR/m²', 'Score', 'Source'];
    var hr = document.createElement('tr');
    headers.forEach(function (name, col) {
      var th = document.createElement('th');
      th.className = 'sort-th';
      if (col >= 2 && col <= 8) th.style.textAlign = 'right';
      th.textContent = name;
      th.onclick = (function (c) {
        return function () { sortTable('flat-custom', c); };
      })(col);
      hr.appendChild(th);
    });
    table.appendChild(hr);
    matches.forEach(function (r) {
      var tr = document.createElement('tr');
      var rKey = (r[idx.source] || '') + ':' + (r[idx.id] || '');
      if (rKey !== ':') tr.setAttribute('id', 'r-' + rKey);
      tr.appendChild(cell(r[idx.district] || '?', r[idx.district]));
      tr.appendChild(cell(r[idx.street] || '—', r[idx.street]));
      var km = r[idx.school_km];
      tr.appendChild(cell(km != null ? km.toFixed(1) + ' km' : '—',
        km != null ? km : 9999, true));
      tr.appendChild(cell(String(r[idx.rooms] == null ? '—' : r[idx.rooms]), r[idx.rooms], true));
      tr.appendChild(cell(String(r[idx.area_m2] == null ? '—' : r[idx.area_m2]), r[idx.area_m2], true));
      tr.appendChild(cell(String(r[idx.floor] == null ? '—' : r[idx.floor]), r[idx.floor], true));
      tr.appendChild(cell(fmtEur(r[idx.price_eur]), r[idx.price_eur], true));
      tr.appendChild(cell(r[idx.price_per_m2] != null
        ? Math.round(r[idx.price_per_m2]).toLocaleString('en-US') : '—',
        r[idx.price_per_m2], true));
      var score = r[idx.score];
      var scTd = cell(score != null ? (+score).toFixed(2) : '—', score, true);
      if (idx._pred_price != null && r[idx._pred_price]) {
        scTd.appendChild(document.createElement('br'));
        var est = document.createElement('span');
        est.style.cssText = 'font-size:10px;color:var(--muted);font-weight:normal';
        est.title = 'Regression model fair-value estimate';
        est.textContent = 'est. ' + fmtEur(r[idx._pred_price]);
        scTd.appendChild(est);
      }
      tr.appendChild(scTd);
      var srcTd = cell(r[idx.source] || '', r[idx.source]);
      var star = document.createElement('button');
      star.type = 'button';
      star.className = 'watch-star';
      star.setAttribute('data-key', rKey);
      star.setAttribute('data-label',
        ((r[idx.street] || r[idx.district] || '?') + ' · ' +
         (r[idx.rooms] || '?') + ' r · ' +
         (r[idx.area_m2] || '?') + ' m²'));
      star.setAttribute('data-price', r[idx.price_eur]);
      star.setAttribute('data-url', r[idx.url] || '');
      star.title = 'Watch this listing';
      star.textContent = '☆';
      srcTd.textContent = '';
      srcTd.appendChild(star);
      if (r[idx.url]) {
        var a = document.createElement('a');
        a.href = r[idx.url];
        a.textContent = r[idx.source] || 'link';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        srcTd.appendChild(a);
      }
      if (idx.also_on != null && Array.isArray(r[idx.also_on])) {
        r[idx.also_on].forEach(function (ao) {
          var nm = (ao && ao.source) ? ao.source : String(ao || '');
          if (!nm) return;
          var el = (ao && ao.url) ? document.createElement('a') : document.createElement('span');
          el.className = 'badge b-src';
          if (ao && ao.url) { el.href = ao.url; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
          el.textContent = 'also on ' + nm;
          srcTd.appendChild(document.createTextNode(' '));
          srcTd.appendChild(el);
        });
      }
      if (idx.also_cheaper != null && r[idx.also_cheaper] && r[idx.also_cheaper].price && r[idx.price_eur]) {
        var chDiff = r[idx.price_eur] - r[idx.also_cheaper].price;
        if (chDiff > 0) {
          var chEl = r[idx.also_cheaper].url ? document.createElement('a') : document.createElement('span');
          chEl.className = 'badge b-cheap';
          if (r[idx.also_cheaper].url) {
            chEl.href = r[idx.also_cheaper].url;
            chEl.target = '_blank';
            chEl.rel = 'noopener noreferrer';
          }
          chEl.textContent = '−' + fmtEur(chDiff) + ' on ' + (r[idx.also_cheaper].source || '?');
          srcTd.appendChild(document.createTextNode(' '));
          srcTd.appendChild(chEl);
        }
      }
      // Signal chips — same motivated/relisted/district badges the
      // daily tables show (fields appended at _FLAT_FIELDS tail).
      var chips = [
        [idx._drop_eur, 'b-cheap', function(v){return '−€'+Math.round(v).toLocaleString('en-US');}],
        [idx._mot, 'b-mot', function(){return 'MOTIVATED';}],
        [idx._at_low, 'b-low', function(){return 'LOWEST SEEN';}],
        [idx._relisted_price, 'b-relist', function(v){
          return 'RELISTED' + (v ? ' · was €'+Math.round(v).toLocaleString('en-US') : '');}],
        [idx._vs_district_pct, 'b-cheap', function(v){
          return v <= -10 ? v+'% vs district' : null;}],
        [idx._vs_district_pct, 'b-mot', function(v){
          return v >= 20 ? '+'+v+'% vs district' : null;}]
      ];
      chips.forEach(function(c){
        var i = c[0]; if (i == null) return;
        var v = r[i]; if (v == null || v === 0) return;
        var txt = c[2](v, r); if (!txt) return;
        var s = document.createElement('span');
        s.className = 'badge ' + c[1];
        s.textContent = txt;
        srcTd.appendChild(document.createTextNode(' '));
        srcTd.appendChild(s);
      });
      var flStr = String(r[idx.floor] || '');
      var flm = flStr.match(/^(\\d+)\\s*\\/\\s*(\\d+)$/);
      if (flm) {
        var fln = parseInt(flm[1], 10), flt = parseInt(flm[2], 10);
        var flTag = (fln === 1 && flt > 1) ? '1st fl' : ((flt > 1 && fln === flt) ? 'top fl' : '');
        if (flTag) {
          var flBadge = document.createElement('span');
          flBadge.className = 'badge b-src';
          flBadge.textContent = flTag;
          srcTd.appendChild(document.createTextNode(' '));
          srcTd.appendChild(flBadge);
        }
      }
      if (r[idx.lat] != null && r[idx.lon] != null &&
          typeof showOnMap === 'function') {
        var ml = document.createElement('a');
        ml.href = '#';
        ml.textContent = 'map';
        ml.style.cssText = 'font-size:11px;color:var(--link)';
        ml.setAttribute('data-key', rKey);
        ml.onclick = function () {
          showOnMap(this.getAttribute('data-key'));
          return false;
        };
        srcTd.appendChild(document.createTextNode(' '));
        srcTd.appendChild(ml);
      }
      // 'seen N d' + own trail — the same line the car custom view prints.
      var hbits = [];
      var fs = r[idx._first_seen];
      if (fs) {
        var ft = Date.parse(String(fs) + 'T00:00:00Z');
        if (!isNaN(ft)) {
          var fdd = Math.max(0, Math.round((refNowMs - ft) / 86400000));
          hbits.push(fdd > 0 ? 'seen ' + fdd + ' d' : 'seen today');
        }
      }
      var hist = r[idx._price_hist] || [];
      if (hist.length >= 2) {
        var htail = hist.slice(-4).map(function (h) { return fmtEur(h[1]); });
        hbits.push((hist.length > 4 ? '… ' : '') + htail.join(' → '));
      }
      if (hbits.length) {
        srcTd.appendChild(document.createElement('br'));
        var hsp = document.createElement('span');
        hsp.style.color = 'var(--muted)';
        hsp.style.fontSize = '12px';
        hsp.textContent = hbits.join(' · ');
        srcTd.appendChild(hsp);
        var fSpark = sparkEl(hist);
        if (fSpark) srcTd.appendChild(fSpark);
      }
      tr.appendChild(srcTd);
      table.appendChild(tr);
    });
    customView.appendChild(table);
    if (typeof window !== 'undefined' && window.__flatWatchRefresh) {
      window.__flatWatchRefresh();
    }
  }

  function syncUrl(rawMax, minRaw, f) {
    if (typeof history === 'undefined' || !history.replaceState ||
        typeof location === 'undefined') return;
    try {
      var p = new URLSearchParams();
      if (minRaw) p.set('min', minRaw);
      if (rawMax) p.set('max', rawMax);
      if (f && f.district) p.set('district', f.district);
      if (f && f.rooms) p.set('rooms', f.rooms);
      if (f && f.minArea) p.set('area', String(f.minArea));
      if (f && f.maxKm) p.set('km', String(f.maxKm));
      if (f && f.midFloor) p.set('midfloor', '1');
      var q = p.toString();
      history.replaceState(null, '',
        (location.pathname || '') + (q ? '?' + q : '') + (location.hash || ''));
    } catch (e) {}
  }

  function hide() {
    customView.style.display = 'none';
    customView.innerHTML = '';
    if (statusEl) statusEl.textContent = '';
    syncUrl('', '', null);
  }

  var timer = null;
  function apply() {
    var raw = String(input.value || '').trim();
    var maxPrice = parseInt(raw, 10);
    var minRaw = minInput ? String(minInput.value || '').trim() : '';
    var floor = parseInt(minRaw, 10);
    var floorSet = !isNaN(floor) && floor > 0;
    var filters = readFilters();
    var filtered = anyFilterSet(filters);
    if ((!raw || isNaN(maxPrice)) && !floorSet && !filtered) {
      hide(); return;
    }
    syncUrl(!isNaN(maxPrice) ? String(maxPrice) : '',
            floorSet ? String(floor) : '', filters);
    if (isNaN(maxPrice)) maxPrice = cfg.maxPrice;  // filters alone
    maxPrice = Math.max(cfg.minPrice, Math.min(cfg.maxPrice, maxPrice));
    var matches = rows.filter(function (r) {
      return r[idx.price_eur] != null && r[idx.price_eur] <= maxPrice &&
             (!floorSet || r[idx.price_eur] >= floor) &&
             passesFilters(r, filters);
    });
    matches.sort(function (a, b) {
      var sa = a[idx.score], sb = b[idx.score];
      if (sa == null && sb == null) return 0;
      if (sa == null) return 1;
      if (sb == null) return -1;
      return sb - sa;
    });
    render(matches, maxPrice);
    customView.style.display = '';
    if (statusEl) {
      statusEl.textContent = matches.length + ' of ' + rows.length +
        ' listings within €' +
        (floorSet ? floor : cfg.minPrice).toLocaleString('en-US') + '–€' +
        maxPrice.toLocaleString('en-US');
    }
  }

  function scheduleApply() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 150);
  }
  [input, minInput, areaInput, kmInput].forEach(function (el) {
    if (el) el.addEventListener('input', scheduleApply);
  });
  [districtSel, roomsSel, midFloorChk].forEach(function (el) {
    if (el) el.addEventListener('change', scheduleApply);
  });
  var resetBtn = document.getElementById('flat-budget-reset');
  if (resetBtn) resetBtn.addEventListener('click', function () {
    input.value = '';
    if (minInput) minInput.value = '';
    [districtSel, roomsSel, areaInput, kmInput].forEach(function (el) {
      if (el) el.value = '';
    });
    if (midFloorChk) midFloorChk.checked = false;
    hide();
  });
  var okBtn = document.getElementById('flat-budget-ok');
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
  var urlMin = params.get('min');
  if (urlMin && minInput && !isNaN(parseInt(urlMin, 10))) {
    minInput.value = urlMin;
  }
  var urlActive = !!(urlMax || urlMin);
  ['district', 'rooms', 'area', 'km'].forEach(function (name) {
    var el = document.getElementById('flat-filter-' + name);
    var v = params.get(name);
    if (v && el) { el.value = v; urlActive = true; }
  });
  if (params.get('midfloor') === '1' && midFloorChk) {
    midFloorChk.checked = true;
    urlActive = true;
  }
  if (urlActive) apply();
  if (typeof window !== 'undefined') {
    window.__flatBudget = { apply: apply, readFilters: readFilters,
                            passesFilters: passesFilters, idx: idx };
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __flatBudgetInit);
} else {
  __flatBudgetInit();
}
"""
