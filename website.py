# -*- coding: utf-8 -*-
"""
Build the hosted GitHub Pages site from saved digests.

  docs/index.html         <- copy of the latest digest (homepage)
  docs/cars.html          <- copy of the latest car digest (Cars tab), or a
                           "Not generated yet" placeholder until the first run
  docs/archive.html       <- auto-generated index of all past digests
  docs/archive/digest_YYYY-MM-DD.html <- copies of every digest ever saved
  docs/archive/cars_YYYY-MM-DD.html   <- copies of every car digest ever saved

Digests older than config.ARCHIVE_KEEP_DAYS are pruned from both data/digests/
and docs/archive/ on every build so the repo does not grow without bound.

The daily workflow commits data/ + docs/ back to the repo and then deploys
docs/ to GitHub Pages itself (pushes made with GITHUB_TOKEN do not trigger
pages.yml, which only serves manual/code pushes).
"""
import os
import re
import shutil
from datetime import date, timedelta
from html import escape

import config
import car_market
import flat_market
import utils
import web_style


DOCS_DIR = os.path.join(config.BASE_DIR, "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")
DIGEST_FILE_RE = re.compile(r"^(?:digest|cars)_(\d{4}-\d{2}-\d{2})\.html$")


def _ensure_dirs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(config.DIGEST_DIR, exist_ok=True)


def prune_old_digests(today=None, keep_days=None):
    """Delete digest files older than keep_days from data/digests/ and
    docs/archive/. Returns the number of files removed. The newest digest of
    each kind is always kept so the site never loses its current page."""
    keep_days = config.ARCHIVE_KEEP_DAYS if keep_days is None else keep_days
    if not keep_days or keep_days <= 0:
        return 0
    today = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    removed = 0
    for folder in (config.DIGEST_DIR, ARCHIVE_DIR):
        if not os.path.isdir(folder):
            continue
        by_kind = {}
        for f in os.listdir(folder):
            m = DIGEST_FILE_RE.match(f)
            if m:
                by_kind.setdefault(f.split("_", 1)[0], []).append((m.group(1), f))
        for kind, files in by_kind.items():
            files.sort()
            newest = files[-1][1]
            for d, f in files:
                if d < cutoff and f != newest:
                    try:
                        os.remove(os.path.join(folder, f))
                        removed += 1
                    except OSError:
                        pass
    if removed:
        print(f"[site] pruned {removed} digest file(s) older than {keep_days} days")
    return removed


def _digest_date(filename, kind):
    """'digest_2026-09-04.html' -> '2026-09-04' for kind 'digest' (or
    'cars'). One regex (DIGEST_FILE_RE) serves both kinds."""
    if not filename.startswith(kind + "_"):
        return None
    m = DIGEST_FILE_RE.match(filename)
    return m.group(1) if m else None


NAV_MARK = 'class="site-nav"'


def _nav_html(prefix, active):
    """Pill-link Flats/Cars/Market/Archive tabs (no JS). prefix is '' in
    docs/ and '../' inside docs/archive/ so links stay relative and
    Pages-subpath safe. Styled by .site-nav in web_style.BASE_CSS."""
    def _cls(tab):
        return " class='on'" if active == tab else ""
    flats_cur = ' aria-current="page"' if active == "flats" else ""
    cars_cur = ' aria-current="page"' if active == "cars" else ""
    market_cur = ' aria-current="page"' if active == "market" else ""
    return (
        f'<style>{web_style.NAV_CSS}</style>'
        '<nav class="site-nav" aria-label="Sections">'
        f'<a href="{prefix}index.html"{_cls("flats")}{flats_cur}>Flats</a>'
        f'<a href="{prefix}cars.html"{_cls("cars")}{cars_cur}>Cars</a>'
        f'<a href="{prefix}market.html"{_cls("market")}{market_cur}>Market</a>'
        f'<a href="{prefix}archive.html">Archive</a>'
        '</nav>\n'
    )


def _real_body_tag(content):
    """First <body...> tag NOT inside a <script>/<style> block or HTML
    comment — JS comments can legitimately contain the literal text
    '<body>' (the budget-script comment does), and injecting the nav
    there would hide it inside the script element."""
    hidden = [m.span() for m in re.finditer(
        r"<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>"
        r"|<!--.*?-->",
        content, re.S | re.I)]
    for m in re.finditer(r"<body[^>]*>", content, re.I):
        if not any(s <= m.start() < e for s, e in hidden):
            return m
    return None


_DAYNAV_MARK = "<div class='daynav'>"
_DAYNAV_RE = re.compile(r"<div class='daynav'>.*?</div>\n?", re.S)


def _day_nav_html(prev_href, prev_label, next_href, next_label, current):
    """'← 2026-10-02 | 2026-10-04 | 2026-10-05 →' bar between dated digest
    copies. Absent sides render a muted dash (first/last day)."""
    prev = (f"<a href='{escape(prev_href)}'>&larr; {escape(prev_label)}</a>"
            if prev_href else "<span class='off'>&larr; —</span>")
    nxt = (f"<a href='{escape(next_href)}'>{escape(next_label)} &rarr;</a>"
           if next_href else "<span class='off'>— &rarr;</span>")
    return (f"<div class='daynav'>{prev}"
            f"<span class='cur'>{escape(current)}</span>{nxt}</div>\n")


def _refresh_daynav(path, daynav_html):
    """Insert or replace the day-navigation bar right after the site nav.
    Recomputed every build so links never point at pruned days — unlike
    _inject_nav this is NOT insert-once, it drifts with the archive."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return
    stripped = _DAYNAV_RE.sub("", content)
    if "</nav>" in stripped:
        new = stripped.replace("</nav>", "</nav>\n" + daynav_html, 1)
    else:
        m = _real_body_tag(stripped)
        new = (stripped[:m.end()] + "\n" + daynav_html + stripped[m.end():]
               if m else daynav_html + stripped)
    if new != content:
        try:
            utils.write_text(path, new)
        except OSError:
            pass


def _inject_nav(path, active, prefix, extra_top=""):
    """Insert the tab bar at the top of <body> of a hosted copy. Idempotent:
    files already carrying the nav are left untouched."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return
    if NAV_MARK in content:
        return
    nav = extra_top + _nav_html(prefix, active)
    m = _real_body_tag(content)
    if m:
        content = content[:m.end()] + "\n" + nav + content[m.end():]
    else:
        content = nav + content
    try:
        utils.write_text(path, content)
    except OSError:
        pass


def _stale_digest_banner(label, digest_date, today):
    """Prominent warning shown on a tab when its newest digest is not
    today's (e.g. the daily run failed before saving)."""
    return (
        f"<div class='stale-warning'>"
        f"<b>Warning: no fresh {escape(label)} digest for {escape(today)} — "
        f"this page shows the latest available digest from "
        f"{escape(digest_date)} and may be stale.</b></div>\n"
    )


def _cars_placeholder_html():
    """Shown on the Cars tab until the first car digest has been generated."""
    _STYLE = web_style.style_block()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">  <!-- no favicon file -> no 404 noise -->
<title>Riga car deals</title>
{_STYLE}</head><body>
{web_style.THEME_TOGGLE_HTML}
{web_style.TOP_BTN_HTML}
{_nav_html("", "cars")}
<h1>Riga car deals</h1>
<p>Not generated yet — the car digest has not run yet.</p>
<p><a href="archive.html">Browse the archive</a></p>
</body></html>"""


_EMBED_RES = (
    re.compile(
        r'<script type="application/json" id="car-market-data">.*?</script>',
        re.S),
    re.compile(
        r'<script type="application/json" id="flat-listings-data">.*?</script>',
        re.S),
)


def _strip_archive_embeds():
    """Remove the embedded market JSON from archived digests.

    The car embed is ~800 KB of JSON per day — ~290 MB/yr of git churn —
    and the flat embed adds its own tens of KB/day, while the budget/watch
    tools they feed are live-page features. Archive copies keep their
    rendered tables (the leftover JS degrades gracefully); the originals
    in data/digests/ and docs/{index,cars}.html keep the embed.
    """
    if not os.path.isdir(ARCHIVE_DIR):
        return
    for f in os.listdir(ARCHIVE_DIR):
        if not (f.endswith(".html")
                and (f.startswith("cars_") or f.startswith("digest_"))):
            continue
        path = os.path.join(ARCHIVE_DIR, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        stripped = content
        for rx in _EMBED_RES:
            stripped = rx.sub("", stripped)
        if stripped == content:
            continue
        try:
            utils.write_text(path, stripped)
        except OSError:
            pass


def _extract_summary(html):
    """Extract a short summary (deal counts) from a digest HTML file."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    # try to find "X new/changed" and "Y still active" from the subject/comparison
    main = re.search(r"(\d+)\s+new\s*/?\s*changed", text, re.I)
    still = re.search(r"(\d+)\s+still\s*active", text, re.I)
    parts = []
    if main:
        parts.append(f"{main.group(1)} new/changed")
    if still:
        parts.append(f"{still.group(1)} still active")
    return ", ".join(parts) if parts else ""


def _extract_car_summary(html):
    """Short summary for a car digest: qualifying-deal count + badge bits."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    parts = []
    qual = re.search(r"All qualifying deals \((\d+)\)", text)
    if qual:
        parts.append(f"{qual.group(1)} qualifying")
    elif re.search(r"No qualifying deals today", text):
        parts.append("0 qualifying")
    day = re.search(r"Today: ([^.]+)\.", text)
    if day:
        parts.append(day.group(1).strip())
    return ", ".join(parts)


def build():
    """Copy the latest flat digest to docs/index.html and the latest car
    digest to docs/cars.html, mirror both into docs/archive/, then
    regenerate docs/archive.html with links to all past digests.
    The originals in data/digests/ are never modified — the Flats/Cars tab
    bar is injected only into the hosted copies."""
    _ensure_dirs()
    today = date.today().isoformat()
    prune_old_digests(today)

    # find the latest digest
    digests = sorted(
        f for f in os.listdir(config.DIGEST_DIR)
        if f.startswith("digest_") and f.endswith(".html")
    )
    archive_name = None
    latest_date = None
    flat_stale_banner = ""
    if digests:
        latest = digests[-1]
        latest_path = os.path.join(config.DIGEST_DIR, latest)
        latest_date = _digest_date(latest, "digest") or today

        # copy latest -> docs/index.html (homepage)
        shutil.copy2(latest_path, os.path.join(DOCS_DIR, "index.html"))

        # copy latest -> docs/archive/digest_YYYY-MM-DD.html
        archive_name = f"digest_{latest_date}.html"
        shutil.copy2(latest_path, os.path.join(ARCHIVE_DIR, archive_name))

        # also copy any digests that exist in data/digests/ but not yet in docs/archive/
        for f in digests:
            dest = os.path.join(ARCHIVE_DIR, f)
            if not os.path.exists(dest):
                shutil.copy2(os.path.join(config.DIGEST_DIR, f), dest)
        if latest_date != today:
            flat_stale_banner = _stale_digest_banner("flat", latest_date, today)
    else:
        print("[site] no flat digests found — skipping index.html")

    car_digests = sorted(
        f for f in os.listdir(config.DIGEST_DIR)
        if f.startswith("cars_") and f.endswith(".html")
    )
    car_archive_name = None
    car_stale_banner = ""
    if car_digests:
        latest_car = car_digests[-1]
        latest_car_path = os.path.join(config.DIGEST_DIR, latest_car)
        car_date = _digest_date(latest_car, "cars") or today

        shutil.copy2(latest_car_path, os.path.join(DOCS_DIR, "cars.html"))
        car_archive_name = f"cars_{car_date}.html"
        shutil.copy2(latest_car_path, os.path.join(ARCHIVE_DIR, car_archive_name))

        for f in car_digests:
            dest = os.path.join(ARCHIVE_DIR, f)
            if not os.path.exists(dest):
                shutil.copy2(os.path.join(config.DIGEST_DIR, f), dest)
        if car_date != today:
            car_stale_banner = _stale_digest_banner("car", car_date, today)
    else:
        utils.write_text(os.path.join(DOCS_DIR, "cars.html"),
                         _cars_placeholder_html())

    # Archive copies of car digests lose the ~800 KB embedded market JSON
    # (the budget tool is a live-page feature; tables stay intact).
    _strip_archive_embeds()

    # Market tab: per-model stats rendered from the JSON cars.run() writes.
    # Not archived — it is a live view, not a dated digest.
    market_path = os.path.join(DOCS_DIR, "market.html")
    utils.write_text(market_path, car_market.build_page())
    market_stale = ""
    stats_dates = [s.get("date") for s in
                   (car_market.load_stats(), flat_market.load_stats())
                   if s and s.get("date")]
    stale_dates = [d for d in stats_dates if d != today]
    if stale_dates:
        market_stale = _stale_digest_banner(
            "market", min(stale_dates), today)
    _inject_nav(market_path, "market", "", extra_top=market_stale)

    _inject_nav(os.path.join(DOCS_DIR, "index.html"), "flats", "",
                extra_top=flat_stale_banner)
    _inject_nav(os.path.join(DOCS_DIR, "cars.html"), "cars", "",
                extra_top=car_stale_banner)

    # Prev/next day navigation: recomputed on every build (so links never
    # point at pruned days) into hosted copies only.
    arch_flat_dates = sorted(
        d for f in os.listdir(ARCHIVE_DIR)
        for d in [_digest_date(f, "digest")] if f.startswith("digest_") and d)
    arch_car_dates = sorted(
        d for f in os.listdir(ARCHIVE_DIR)
        for d in [_digest_date(f, "cars")] if f.startswith("cars_") and d)
    if latest_date:
        _refresh_daynav(
            os.path.join(DOCS_DIR, "index.html"),
            _day_nav_html(
                (f"archive/digest_{arch_flat_dates[-2]}.html"
                 if len(arch_flat_dates) > 1 else None),
                (arch_flat_dates[-2] if len(arch_flat_dates) > 1 else None),
                None, None, latest_date))
    if car_digests:
        _refresh_daynav(
            os.path.join(DOCS_DIR, "cars.html"),
            _day_nav_html(
                (f"archive/cars_{arch_car_dates[-2]}.html"
                 if len(arch_car_dates) > 1 else None),
                (arch_car_dates[-2] if len(arch_car_dates) > 1 else None),
                None, None, car_date))

    def _arch_daynav(dates, current, kind, live_href):
        """Prev/next links inside an archive copy; the newest one points
        forward at the live tab."""
        i = dates.index(current) if current in dates else -1
        prev_d = dates[i - 1] if i > 0 else None
        if 0 <= i < len(dates) - 1:
            next_href, next_label = f"{kind}{dates[i + 1]}.html", dates[i + 1]
        elif i == len(dates) - 1:
            next_href, next_label = live_href, "latest"
        else:
            next_href = next_label = None
        return _day_nav_html(
            f"{kind}{prev_d}.html" if prev_d else None, prev_d,
            next_href, next_label, current)

    for f in os.listdir(ARCHIVE_DIR):
        if f.startswith("digest_") and f.endswith(".html"):
            p = os.path.join(ARCHIVE_DIR, f)
            _inject_nav(p, "flats", "../")
            d = _digest_date(f, "digest")
            if d:
                _refresh_daynav(
                    p, _arch_daynav(arch_flat_dates, d, "digest_",
                                    "../index.html"))
        elif f.startswith("cars_") and f.endswith(".html"):
            p = os.path.join(ARCHIVE_DIR, f)
            _inject_nav(p, "cars", "../")
            d = _digest_date(f, "cars")
            if d:
                _refresh_daynav(
                    p, _arch_daynav(arch_car_dates, d, "cars_",
                                    "../cars.html"))

    # generate archive.html
    archive_files = sorted(
        [f for f in os.listdir(ARCHIVE_DIR)
         if f.startswith("digest_") and f.endswith(".html")],
        reverse=True,  # newest first
    )
    car_archive_files = sorted(
        [f for f in os.listdir(ARCHIVE_DIR)
         if f.startswith("cars_") and f.endswith(".html")],
        reverse=True,
    )

    rows = []
    for f in archive_files:
        d = _digest_date(f, "digest") or f
        fpath = os.path.join(ARCHIVE_DIR, f)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                content = fh.read()
            summary = _extract_summary(content)
        except OSError:
            summary = ""
        label = escape(d)
        if f == archive_name:
            label += " (today)" if d == today else " (latest)"
        rows.append(
            f"<tr>"
            f"<td><b><a href='archive/{escape(f)}'>{label}</a></b></td>"
            f"<td class='note'>{escape(summary)}</td>"
            f"</tr>"
        )
    rows_html = ("\n".join(rows) if rows else
                 '<tr><td colspan="2">No digests yet.</td></tr>')

    car_rows = []
    for f in car_archive_files:
        d = _digest_date(f, "cars") or f
        fpath = os.path.join(ARCHIVE_DIR, f)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                car_summary = _extract_car_summary(fh.read())
        except OSError:
            car_summary = ""
        label = escape(d)
        if f == car_archive_name:
            label += " (today)" if d == today else " (latest)"
        car_rows.append(
            f"<tr><td><b>"
            f"<a href='archive/{escape(f)}'>{label}</a></b></td>"
            f"<td class='note'>{escape(car_summary)}</td></tr>"
        )
    car_rows_html = "\n".join(car_rows) if car_rows else \
        '<tr><td colspan="2">Not generated yet.</td></tr>'

    # Coverage strip for the last ARCHIVE_GAP_DAYS days: green = both
    # digests, amber = only one source, red = no scan at all (run failed
    # or never ran). Makes CI gaps visible at a glance.
    flat_dates = {d for d in (_digest_date(f, "digest") for f in archive_files) if d}
    car_dates = {d for d in (_digest_date(f, "cars") for f in car_archive_files) if d}
    strip = []
    for i in range(config.ARCHIVE_GAP_DAYS - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        both = d in flat_dates and d in car_dates
        label = ("flat + car digests" if both else
                 "flat digest only" if d in flat_dates else
                 "car digest only" if d in car_dates else "no scan")
        cls = ("cov-both" if both else
               "cov-one" if label != "no scan" else "cov-none")
        strip.append(
            f"<span class='cov-cell {cls}' title='{d}: {label}'></span>")
    coverage_html = (
        "<div class='card'>" + "".join(strip) +
        f"<div class='note'>Last {config.ARCHIVE_GAP_DAYS} days: "
        "green = both digests, amber = one source only, red = no scan. "
        "Hover a cell for the date.</div></div>")

    _STYLE = web_style.style_block()
    archive_html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">  <!-- no favicon file -> no 404 noise -->
<title>Riga flat & car deals — archive</title>
{_STYLE}</head><body>
{web_style.THEME_TOGGLE_HTML}
{web_style.TOP_BTN_HTML}
{_nav_html("", "")}
<h1>Riga flat & car deals — archive</h1>
<p class="note">Districts: {', '.join(config.DISTRICTS.keys())} · Sources: ss.com, city24.lv, pp.lv</p>
{coverage_html}
<p><a href="index.html">← Back to today's deals</a></p>
<div class='card'><h2>Flat digests ({len(archive_files)} total)</h2>
<table>
<tr><th>Date</th><th>Summary</th></tr>
{rows_html}
</table></div>
<div class='card'><h2>Car digests ({len(car_archive_files)} total)</h2>
<table>
<tr><th>Date</th><th>Summary</th></tr>
{car_rows_html}
</table></div>
<hr><p class="note">Generated by Flat_Searcher on {today}. Digests are kept
for {config.ARCHIVE_KEEP_DAYS} days.</p>
</body></html>"""

    utils.write_text(os.path.join(DOCS_DIR, "archive.html"), archive_html)

    print(f"[site] built: index.html ({'digest ' + latest_date if latest_date else 'no flat digest'}), cars.html "
          f"({car_archive_name or 'placeholder'}), archive.html "
          f"({len(archive_files)} flat + {len(car_archive_files)} car digests)")


if __name__ == "__main__":
    build()
