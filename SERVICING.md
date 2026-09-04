# SERVICING — Flat_Searcher

Living document. Updated after each Devin session. Read this first.

## Current state (after session 2026-09-05, upgrade #9 — daily rental detection + map fix)

**Working, tested end-to-end locally on Windows + Python 3.14.4.**
Pipeline scrapes 278 SS.com + 29 city24.lv = 307 listings daily.

### Upgrade 9 (this session): daily rental detection + map coverage fix

**Daily rental detection:**
- SS.com price text shows "EUR/day" for daily rentals and "EUR/mon." for
  monthly. The scraper now captures `price_unit` ("day" or "mon") from
  the price cell text.
- Daily rentals show "60 EUR/day" in the price column instead of "60 EUR".
- Daily rentals are excluded from the exceptional deals composite score
  (they always win on price since 60 EUR/day looks like an impossibly
  cheap monthly rent).
- Daily rentals are excluded from the regression model training baseline
  and district median calculations.
- Daily rentals in the main deals section get a SHORT-TERM/DAILY badge.
- Exceptional deals section description notes that short-term/daily
  rentals are excluded from the ranking.
- `price_unit` added to HISTORY_COLUMNS and city24 scraper output.

**CenuMednieks deal-type mismatch (also fixed in exceptional.py):**
- `_get_price_drop_pct` now applies the same 5x ratio sanity check as
  `get_price_timeline` and `_get_listing_age`. A 275,000 EUR "original
  price" on a 1,100 EUR rental is filtered out instead of producing a
  distorted composite score.

**Map coverage fix:**
- Map markers were built from `all_scored` (top 10 per deal type = 20
  listings), not `all_listings` (261 listings). Fixed by building markers
  from `all_listings`. Map went from 15 markers to 203 markers.
- Scores are still attached to map popups where available (looked up
  from `all_scored` by `source:id` key).

### Upgrade 8 (this session): visual readability overhaul + map repositioned

**Table structure:**
- Reduced from 13 to 11 columns (10 for still-active) by merging
  Listed+Days into one column and First price+Change into one.
- Removed the empty Status column from still-active tables.
- Right-aligned all numeric columns (was mixed center/right).
- Deal score is now the boldest element in each row (16px, bold, blue).
- Zebra striping per listing group for easier vertical scanning.
- Fixed m2 -> m² in all headers and cell values.

**Change column:**
- +0.0% is now grey instead of red (was alarm-red on 13 of 20 rows).
- Numeric data-sort preserved for proper sorting.

**Timeline:**
- Single-observation timelines show "Listed at X EUR (date), unchanged"
  instead of "First: X → X" with a no-op arrow.
- Previous ads truncated to 3 most recent + "(+N earlier)" summary
  (was showing up to 17 entries, dominating the row).

**Sort indicators:**
- Removed always-visible ⇓ glyphs from all headers.
- Indicators now appear only on hover (faded) and on the active sorted
  column (directional arrow via JS).

**CenuMednieks deal-type mismatch:**
- original_price and previous ad prices that differ from the current
  price by more than 5x are now filtered out (was showing 275,000 EUR
  "first price" for a 1,100 EUR rental, producing a -99.6% "change").

**Map:**
- Moved from fixed right-side sidebar back to an inline map at the
  bottom of the page (per user request).
- Removed all position:fixed, display:flex, toggleMap JS, and responsive
  sidebar CSS that broke in email clients.

**Email compatibility:**
- Exceptional deal cards now use table-based layout instead of flex
  (Gmail/Outlook ignore flex and margin-left:auto).
- Zero display:flex rules remain in the digest.

### Upgrade 7 (this session): SS.com district pages + sortable tables + map sidebar + 12-issue code review fix

**SS.com scraper overhaul:**
- Was only scraping `/today/` page (0-2 listings at night). Now scrapes
  district-specific pages (`/riga/imanta/hand_over/` etc.) with full
  pagination. Result: 278 SS.com listings (was 0).
- Fixed Sampeteris slug: `shampeteris-pleskodale` (was wrong, returned 0).
- Fixed pagination detection: SS.com uses "Next" text, not `»` symbol.
- `SS_COM_MAX_PAGES` raised from 5 to 15 (Imanta sale has 9+ pages).
- Added `ad_slug` field (alphabetic ID from URL) for CenuMednieks lookups.
- District-specific pages: first cell is street name (not District<br>Street),
  so `forced_district` parameter skips district matching.
- Removed redundant `/today/` page fetch (district pages include today's).

**UI improvements:**
- Sortable table headers (click to sort asc/desc, numeric vs string aware).
- New columns: First price (original listing price), Change (% from first).
- Floating sticky map sidebar (always visible while scrolling on desktop,
  toggle button on mobile).
- Top exceptional deals header (composite score: deal score + price drop +
  days on market + price vs area median). Top 5 shown as ranked cards.
- Timeline rows: light gray background, smaller font, visual separation
  from data rows.
- Previous CenuMednieks ads separated from current ad timeline.

**12 code review fixes:**
1. `first_seen` field in price_history (city24 age was always 0 days).
2. history.csv now records price changes (model was training on stale prices).
3. Hourly escalation uses fewer pages (`SS_COM_MAX_PAGES_HOURLY=3`).
4. Escalation alerts now include price history context.
5. city24 `old_price`/`show_price_drop` captured in history + price tracking.
6. `drop_pct` clamped to ±50% in exceptional score.
7. Dead `_next_page_url` lookup removed.
8. Redundant `/today/` fetch removed.
9. DISTRICTS aliases normalized to lowercase.
10. Unused `price_data` arg removed from `geocode.get_map_data`.
11. `MIN_EXPECTED_LISTINGS` raised from 3 to 50.
12. Change column now has numeric `data-sort` for proper sorting.

### Upgrade 6 (earlier session): price history tracking via CenuMednieks.lv
- CenuMednieks.lv integration for SS.com listings.
- Own daily tracking for all listings.
- Timeline display in digest.
- Weekly refresh caching.
- 1s delay between CenuMednieks requests.

### Upgrade 5 (earlier session): six reliability/ML hardening fixes
1. History leakage fix (`exclude_today=True`).
2. Git push conflict fix (shared concurrency group + rebase-retry).
3. Minimum-history escalation gate (`ESCALATION_MIN_HISTORY=30`).
4. Operator failure alerting (total_zero, source_zero, low_volume).
5. Ridge regression + cardinality caps.
6. Cross-source deduplication.

### Known issues / things to watch
- **SS.com zero listings at night**: the `/today/` page rolls over late at
  night. District pages always have listings, so this is no longer a problem.
- **CenuMednieks PRO features locked**: full historical timelines (every
  individual price change) are behind a paywall. We get original price,
  current price, days on market, and previous ads — enough for a useful
  timeline. Our own daily tracking fills gaps going forward.
- **city24 has no external history source**: price history comes only from
  our own daily observations. First seen date and days on market are correct
  (fixed in this session via `first_seen` field).
- **SMTP not configured**: pipeline runs, saves digest, builds site, skips
  email. Add SMTP secrets to enable email delivery.
- **Scoring uses z-score fallback**: until history reaches 40 rows per deal
  type, the regression model stays in fallback mode. History is growing.

### Key files
- `main.py` — daily pipeline orchestrator
- `escalation.py` — hourly hot-deal scanner
- `config.py` — all configuration
- `scrapers/ss_com.py`, `scrapers/city24.py` — scrapers
- `scoring.py` — regression + z-score fallback
- `classify.py` — new/changed/reappeared/still-active classification
- `history.py` — persistent state (history.csv, seen_deals.json)
- `notifier.py` — HTML digest + email + map + sortable tables
- `exceptional.py` — composite exceptional deal scoring
- `price_history.py` — CenuMednieks + own tracking + timeline formatter
- `geocode.py` — Nominatim geocoding + map data
- `health.py` — operator failure alerting
- `website.py` — GitHub Pages site builder
- `data/` — committed state files (history.csv, price_history.json, etc.)
- `docs/` — generated Pages content (index.html, archive.html, unsubscribe.html)

### Upgrade 1 (earlier today): core pipeline
A full `python -m main` run scraped 33 target-district listings
(ss.com: 4, city24.lv: 29), scored them, saved an HTML digest, and copied a
status prompt to the clipboard. Both scoring paths verified (z-score
fallback + numpy regression).

### Upgrade 2 (this session): deal persistence + price drops + unsubscribe
- **Deal persistence**: top deals that persist across days now appear in a
  greyed "Still active from yesterday" section instead of being hidden. New
  deals get a green NEW badge.
- **Price-drop tracking**: each deal's price is tracked in
  `data/seen_deals.json`. If a previously-shown deal's price drops ≥2%, it's
  resurfaced in the main table with an orange "PRICE DROP — was X EUR (↓Y%)"
  badge.
- **Comparison header**: the email shows "Today's best deal (score +X) beats/
  ties/falls short of yesterday's best (+Y)" per deal type.
- **Unsubscribe**: email footer has a link to a GitHub Pages page
  (`docs/unsubscribe.html`). Confirming adds the email to
  `data/unsubscribed.json`; the daily run skips unsubscribed recipients.

All four features verified locally:
- Migration `seen_ids.json` → `seen_deals.json` ✓ (33 entries migrated)
- REAPPEARED badge ✓ (first run after migration)
- STILL ACTIVE greyed section ✓ (second run, 17 deals persisted)
- Comparison header "vs yesterday" ✓
- PRICE DROP badge ✓ (manually patched a `last_shown_price` to simulate a drop)
- Unsubscribe check ✓ (recipient in `unsubscribed.json` → email skipped, digest saved)

### Upgrade 3 (this session): hosted website + archive + data quality fix
- **Hosted website**: the daily digest is now published to GitHub Pages as a
  live website (`docs/index.html` = latest digest, `docs/archive.html` =
  browsable archive of all past digests). The email includes a "View in
  browser" link. The recipient gets both push (email) and pull (website)
  delivery.
- **Archive page**: `docs/archive.html` auto-generated with clickable links to
  every past digest, sorted newest-first, with deal-count summaries.
- **Data quality fix**: added `MIN_SALE_PRICE_EUR` / `MIN_RENT_PRICE_EUR`
  sanity filters in `config.py` — drops city24 listings with garbage prices
  (e.g. a "sale" listing at 189 EUR was caught and removed).
- **"View in browser" link**: appears at the top of the email when `SITE_URL`
  is set, linking to the hosted site + archive.

Verified locally:
- `website.build()` generates `docs/index.html` + `docs/archive.html` + `docs/archive/` ✓
- Archive page lists all past digests with summaries ✓
- "View in browser" link appears in email when `SITE_URL` is set ✓
- Price sanity filter drops the 189 EUR garbage listing ✓
- Renamed `site.py` → `website.py` to avoid collision with Python's built-in `site` module ✓

### Upgrade 4 (this session): hourly hot-deal escalation scanner
- **Hourly scan**: `escalation.py` runs every hour via `escalation.yml` workflow.
  Scrapes all listings, scores them, and if any deal scores ≥ 2.333 (a
  ~1-in-100 statistical outlier — user-chosen threshold) and hasn't been
  alerted yet, sends an **instant alert email** with the deal link.
- **Alert dedup**: `data/alerted_deals.json` tracks already-alerted deals so
  the same listing isn't re-alerted every hour.
- **Alert email**: distinct format from the daily digest — red-themed, shows
  the deal score, price, €/m², and a direct link. Subject: "HOT DEAL ALERT".
- **Website update on hot deal**: when a hot deal is found, the website is
  rebuilt immediately so the hosted site shows it without waiting for the
  daily run.
- **Daily digest unaffected**: the hourly scan does NOT update seen_deals or
  last_digest (that's the daily job's responsibility).

Verified locally:
- First hourly scan: 1 deal (Imanta 4r 76m² 88,000 EUR, score +2.51) triggered
  alert ✓
- Alert email saved with red theme + deal link ✓
- Deal marked in `alerted_deals.json` ✓
- Second hourly scan: no re-alert (deal already in alerted_deals) ✓

### Pipeline status
| Component | Status | Notes |
|---|---|---|
| `scrapers/ss_com.py` | WORKING | rent uses `hand_over` URL; parses `tr[id^=tr_]` rows |
| `scrapers/city24.py` | WORKING | Playwright intercepts `api.city24.lv/<loc>/search/realties` JSON |
| `history.py` | WORKING | seen_deals, last_digest, unsubscribed, alerted_deals, ops_alerts; `load_history(exclude_today=True)` for leakage-safe training; legacy migration |
| `scoring.py` | WORKING | ridge regression (≥40 rows/type) with cardinality caps + `__other__` bucket; else z-score fallback |
| `classify.py` | WORKING | NEW/PRICE_DROP/STILL_ACTIVE/REAPPEARED badges + comparison header |
| `notifier.py` | WORKING | badges, still-active, comparison header, unsub link + check; operator alert sender |
| `main.py` | WORKING | scrape → dedupe → health check → score → classify → send → build site → update state |
| `website.py` | WORKING | builds docs/index.html + docs/archive.html + docs/archive/ from saved digests |
| `escalation.py` | WORKING | hourly scan with min-history gate (≥30 rows/type) before alerting |
| `health.py` | WORKING (new) | detects total_zero / source_zero / low_volume; throttled operator alerts |
| `utils.py` | WORKING | district matching, slugify, cross-source dedup with `also_on` tracking |
| `price_history.py` | WORKING (new) | CenuMednieks.lv historical backfill + own daily tracking; timeline HTML formatter |
| `docs/index.html` | AUTO-GEN | latest digest = Pages homepage |
| `docs/archive.html` | AUTO-GEN | browsable archive of all past digests |
| `docs/unsubscribe.html` | READY | static page, placeholders injected by pages.yml at deploy |
| `.github/workflows/pages.yml` | READY | deploys docs/ to Pages with PAT injection; not yet exercised on CI |
| `.github/workflows/daily.yml` | READY | shared concurrency group + rebase-retry push; not yet exercised on CI |
| `.github/workflows/escalation.yml` | READY | shared concurrency group + rebase-retry push; min-history gate; not yet exercised on CI |

## Known issues & how to fix

1. **city24.lv listings URL slug** — detail URLs are built as
   `.../apartments-for-{deal}/riga-{district-slug}-{street-slug}/{friendly_id}`.
   If city24 changes its URL scheme, links may 404 but the listing data
   (price/rooms/etc.) is still correct. Fix: adjust `_extract_item` in
   `scrapers/city24.py`.
2. **city24 anti-bot token** — handled automatically by rendering in real
   Chromium. If city24 adds stronger bot protection, the intercept may stop
   capturing JSON. Fix: increase `CITY24_NAV_TIMEOUT`, or switch to reading
   the rendered DOM (selectors: `article.object-wrapper`,
   `.object-price__main-price`, `.icon-door`, `.icon-stairs`).
3. **city24 pagination** — we walk `pg=1..CITY24_MAX_PAGES` (default 8) and
   stop when a page returns <50 items. If city24 changes items-per-page or
   route, adjust `CITY24_SEARCH_URL` / `CITY24_MAX_PAGES` in `config.py`.
4. **Regression vs fallback** — until ≥40 history rows per deal type exist,
   the z-score fallback is used. Sale (~26/day) reaches regression in ~2 days,
   rent (~7/day) in ~6 days. Lower `MIN_TRAIN_ROWS` in `config.py` to force
   regression sooner (noisier).
5. **ss.com "today" cutoff** — only ads posted in the last ~24h are scraped.
   If a run is missed, those ads won't appear later. Acceptable for "daily
   new" use case.
6. **History leakage** — FIXED (upgrade 5): `load_history(exclude_today=True)`
   drops today's rows before training. Both daily and hourly use this. The
   hourly scan can no longer poison the daily baseline.
7. **Git push conflicts** — FIXED (upgrade 5): daily + hourly workflows share
   concurrency group `flat-searcher-state`; push step does pull-rebase-retry
   (5 attempts) and fails loudly instead of silently dropping state.
8. **Escalation with too little history** — FIXED (upgrade 5):
   `ESCALATION_MIN_HISTORY=30` gate per deal type; first days can't fire
   false hot-deal emails.
9. **Scraper silent failure** — FIXED (upgrade 5): `health.py` detects
   zero/low counts and emails the operator (throttled daily). Recipient
   still gets whatever deals were scraped.
10. **Regression overfitting** — FIXED (upgrade 5): ridge L2 penalty +
    cardinality caps + `__other__` bucket prevent rare categories from
    memorising rows.
11. **Cross-source duplicates** — FIXED (upgrade 5): `dedupe_cross_source()`
    merges same-flat listings across ss.com + city24 before scoring/history.
12. **PAT exposure in unsubscribe page** — the `UNSUBSCRIBE_PAT` is embedded
    in client-side JS on the GitHub Pages site (Pages has no secrets for
    static sites). Scoped to `contents: write` on this repo only. The repo
    has no secrets in it (SMTP creds are in GitHub Actions secrets). Worst
    case: someone edits files in a non-sensitive repo or unsubscribes people.
    Rotatable: update the `UNSUBSCRIBE_PAT` secret and re-run the pages
    workflow. See README §"Enable GitHub Pages".
13. **seen_deals.json growth** — dict grows over time. Not a concern for
    months (a few hundred entries). Could add periodic cleanup of entries
    older than 30 days with no re-sighting.
14. **last_digest staleness** — if a run is missed, `last_digest.json` is from
    the last successful run. The comparison header would compare against a
    stale date. The header includes the date so it's clear. Acceptable.

## How to re-run / debug locally
```
cd C:\Users\rudol\CascadeProjects\Flat_Searcher
$env:PYTHONIOENCODING="utf-8"
python -X utf8 -m scrapers.ss_com      # just ss.com
python -X utf8 -m scrapers.city24      # just city24 (needs `playwright install chromium`)
python -X utf8 -m main                 # full pipeline
```
To force the regression path for testing: set `config.MIN_TRAIN_ROWS=5` in a
one-off script (don't commit).
To test PRICE_DROP: edit `data/seen_deals.json`, bump a `last_shown_price`
upward for one deal, re-run.
To test unsubscribe: add an email to `data/unsubscribed.json`, set
`EMAIL_TO` env to that email, re-run → email skipped.

## Data files (committed to repo)
- `data/history.csv` — all unique listings ever scraped (training data).
- `data/seen_deals.json` — `"{source}:{id}"` → metadata (first/last shown,
  last price, last score, deal type). Migrated from legacy `seen_ids.json`.
- `data/seen_ids.json` — legacy (kept for reference; no longer written after
  migration).
- `data/last_digest.json` — yesterday's top deals (for "vs yesterday"
  comparison + still-active detection).
- `data/unsubscribed.json` — list of unsubscribed recipient emails.
- `data/alerted_deals.json` — list of `"{source}:{id}"` already sent as
  hourly escalation alerts (prevents re-alerting the same deal every hour).
- `data/ops_alerts.json` — dict of `issue_key` → last-alerted date; used to
  throttle operator health alerts to once per issue per day.
- `data/price_history.json` — per-listing price history: CenuMednieks cached
  data (SS.com only, refreshed weekly) + our own daily price observations
  (all sources).
- `data/digests/digest_YYYY-MM-DD.html` — saved digests.
- `data/digests/alert_YYYY-MM-DD.html` — saved escalation alerts.

## To enable email (recipient gets daily deals)
Add GitHub repo secrets: `SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS,
EMAIL_FROM, EMAIL_TO`. See README §Setup. Without them, digests are only
saved as HTML files.

## To enable unsubscribe + hosted site
1. Repo → Settings → Pages → Source: GitHub Actions.
2. Create fine-grained PAT (contents: write, this repo only) → add as
   `UNSUBSCRIBE_PAT` secret.
3. Push → `pages.yml` deploys `docs/` to Pages (index.html = latest digest,
   archive.html = archive, unsubscribe.html = unsubscribe page).
4. Set `UNSUBSCRIBE_URL` secret to `https://<owner>.github.io/<repo>/unsubscribe.html`.
5. Set `SITE_URL` secret to `https://<owner>.github.io/<repo>`.
See README §"Enable GitHub Pages".

## Next steps / TODO for next session
- Push to GitHub, add secrets, trigger all three workflows once to verify CI
  (Playwright `install --with-deps chromium` on ubuntu-latest; Pages deploy
  with PAT injection; hourly escalation cron; rebase-retry push behaviour).
- Verify the shared concurrency group works on CI (daily + hourly don't
  collide; second run queues).
- Set `OPS_EMAIL_TO` secret if operator alerts should go to a different
  address than `EMAIL_FROM`.
- Optionally add a 3rd source (inbox.lv) if more coverage needed.
- Optional: "no longer available" notifications (deals from yesterday that
  disappeared = likely sold/rented = market signal).
- Optional: price-history sparklines in the email.
- Optional: Telegram bot delivery as an alternative to email.
- Note on GitHub Actions minutes: hourly scan ~24 runs/day × ~5 min =
  ~120 min/day = ~3600 min/month. Free tier is 2000 min/month for private
  repos, UNLIMITED for public repos. If the repo is public, this is fine.
  If private, consider switching to every 2-3 hours or making the repo public.
