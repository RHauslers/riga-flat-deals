# -*- coding: utf-8 -*-
"""
Build the hosted GitHub Pages site from saved digests.

  docs/index.html         <- copy of the latest digest (homepage)
  docs/cars.html          <- copy of the latest car digest (Cars tab), or a
                           "Not generated yet" placeholder until the first run
  docs/archive.html       <- auto-generated index of all past digests
  docs/archive/digest_YYYY-MM-DD.html <- copies of every digest ever saved
  docs/archive/cars_YYYY-MM-DD.html   <- copies of every car digest ever saved
  docs/unsubscribe.html   <- (untouched, managed by pages.yml)

The daily workflow commits docs/ back to the repo, which triggers pages.yml
to redeploy the site to GitHub Pages.
"""
import os
import re
import shutil
from datetime import date
from html import escape

import config


DOCS_DIR = os.path.join(config.BASE_DIR, "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, "archive")


def _ensure_dirs():
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    os.makedirs(config.DIGEST_DIR, exist_ok=True)


def _extract_date(filename):
    """'digest_2026-09-04.html' -> '2026-09-04'."""
    m = re.match(r"digest_(\d{4}-\d{2}-\d{2})\.html", filename)
    return m.group(1) if m else None


def _extract_car_date(filename):
    """'cars_2026-09-04.html' -> '2026-09-04'."""
    m = re.match(r"cars_(\d{4}-\d{2}-\d{2})\.html", filename)
    return m.group(1) if m else None


NAV_MARK = 'class="site-nav"'


def _nav_html(prefix, active):
    """Plain-link Flats/Cars tabs (no JS). prefix is '' in docs/ and '../'
    inside docs/archive/ so links stay relative and Pages-subpath safe."""
    style_base = "display:inline-block;padding:8px 14px;margin-right:4px;border-radius:4px;text-decoration:none"
    style_on = "background:#1a5276;color:#fff;font-weight:bold"
    style_off = "background:#eaf1f7;color:#1a5276"
    flats_cur = ' aria-current="page"' if active == "flats" else ""
    cars_cur = ' aria-current="page"' if active == "cars" else ""
    return (
        f'<nav class="site-nav" aria-label="Sections" '
        f'style="margin:0 0 16px 0">'
        f'<a href="{prefix}index.html"{flats_cur} '
        f'style="{style_base};{style_on if active == "flats" else style_off}">Flats</a>'
        f'<a href="{prefix}cars.html"{cars_cur} '
        f'style="{style_base};{style_on if active == "cars" else style_off}">Cars</a>'
        f'<a href="{prefix}archive.html" '
        f'style="{style_base};{style_off}">Archive</a>'
        f'</nav>\n'
    )


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
    m = re.search(r"<body[^>]*>", content, re.I)
    if m:
        content = content[:m.end()] + "\n" + nav + content[m.end():]
    else:
        content = nav + content
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


def _stale_digest_banner(label, digest_date, today):
    """Prominent warning shown on a tab when its newest digest is not
    today's (e.g. the daily run failed before saving)."""
    return (
        f"<div class='stale-warning' style='background:#fdecea;"
        f"border:1px solid #c0392b;padding:10px 14px;margin:12px 0'>"
        f"<b>Warning: no fresh {escape(label)} digest for {escape(today)} — "
        f"this page shows the latest available digest from "
        f"{escape(digest_date)} and may be stale.</b></div>\n"
    )


def _cars_placeholder_html():
    """Shown on the Cars tab until the first car digest has been generated."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga car deals</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:800px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}a{{color:#2874a6}}
</style></head><body>
{_nav_html("", "cars")}
<h1>Riga car deals</h1>
<p>Not generated yet — the car digest has not run yet.</p>
<p><a href="archive.html">Browse the archive</a></p>
</body></html>"""


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


def build():
    """Copy the latest flat digest to docs/index.html and the latest car
    digest to docs/cars.html, mirror both into docs/archive/, then
    regenerate docs/archive.html with links to all past digests.
    The originals in data/digests/ are never modified — the Flats/Cars tab
    bar is injected only into the hosted copies."""
    _ensure_dirs()
    today = date.today().isoformat()

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
        latest_date = _extract_date(latest) or today

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
        car_date = _extract_car_date(latest_car) or today

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
        with open(os.path.join(DOCS_DIR, "cars.html"), "w",
                  encoding="utf-8") as f:
            f.write(_cars_placeholder_html())

    _inject_nav(os.path.join(DOCS_DIR, "index.html"), "flats", "",
                extra_top=flat_stale_banner)
    _inject_nav(os.path.join(DOCS_DIR, "cars.html"), "cars", "",
                extra_top=car_stale_banner)
    for f in os.listdir(ARCHIVE_DIR):
        if f.startswith("digest_") and f.endswith(".html"):
            _inject_nav(os.path.join(ARCHIVE_DIR, f), "flats", "../")
        elif f.startswith("cars_") and f.endswith(".html"):
            _inject_nav(os.path.join(ARCHIVE_DIR, f), "cars", "../")

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
        d = _extract_date(f) or f
        fpath = os.path.join(ARCHIVE_DIR, f)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                content = fh.read()
            summary = _extract_summary(content)
        except OSError:
            summary = ""
        label = d
        if f == archive_name:
            label += " (today)" if d == today else " (latest)"
        rows.append(
            f"<tr>"
            f"<td style='padding:8px'><a href='archive/{escape(f)}'>{escape(label)}</a></td>"
            f"<td style='padding:8px;color:#666'>{escape(summary)}</td>"
            f"</tr>"
        )
    rows_html = "\n".join(rows) if rows else "<tr><td>No digests yet.</td></tr>"

    car_rows = []
    for f in car_archive_files:
        d = _extract_car_date(f) or f
        label = d
        if f == car_archive_name:
            label += " (today)" if d == today else " (latest)"
        car_rows.append(
            f"<tr><td style='padding:8px'>"
            f"<a href='archive/{escape(f)}'>{escape(label)}</a></td></tr>"
        )
    car_rows_html = "\n".join(car_rows) if car_rows else \
        "<tr><td>Not generated yet.</td></tr>"

    archive_html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Riga flat & car deals — archive</title>
<style>
body{{font-family:Arial,sans-serif;color:#222;max-width:800px;margin:0 auto;padding:20px}}
h1{{color:#1a5276}}h2{{color:#2874a6}}
td,th{{border-bottom:1px solid #eee}}a{{color:#2874a6;text-decoration:none}}a:hover{{text-decoration:underline}}
.note{{color:#777;font-size:13px}}
table{{width:100%;border-collapse:collapse}}
</style></head><body>
{_nav_html("", "")}
<h1>Riga flat & car deals — archive</h1>
<p class="note">Districts: {', '.join(config.DISTRICTS.keys())} · Sources: ss.com, city24.lv, pp.lv</p>
<p><a href="index.html">← Back to today's deals</a></p>
<h2>Flat digests ({len(archive_files)} total)</h2>
<table>
<tr style="background:#f0f0f0"><th style="text-align:left;padding:8px">Date</th><th style="text-align:left;padding:8px">Summary</th></tr>
{rows_html}
</table>
<h2>Car digests ({len(car_archive_files)} total)</h2>
<table>
<tr style="background:#f0f0f0"><th style="text-align:left;padding:8px">Date</th></tr>
{car_rows_html}
</table>
<hr><p class="note">Generated by Flat_Searcher on {today}.
<a href="unsubscribe.html">Unsubscribe from emails</a></p>
</body></html>"""

    with open(os.path.join(DOCS_DIR, "archive.html"), "w", encoding="utf-8") as f:
        f.write(archive_html)

    print(f"[site] built: index.html (digest {latest_date}), cars.html "
          f"({car_archive_name or 'placeholder'}), archive.html "
          f"({len(archive_files)} flat + {len(car_archive_files)} car digests)")


if __name__ == "__main__":
    build()
