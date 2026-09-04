# SERVICING — Flat_Searcher

Living document. Updated after each Devin session. Read this first.

## Current state (after session 2026-09-04, upgrade #5 — hardening)

**Working, tested end-to-end locally on Windows + Python 3.14.4.**
All six hardening fixes shipped and verified. The pipeline now has:
leakage-safe training, conflict-safe CI commits, a min-history escalation
gate, operator failure alerts, ridge-regularised regression with cardinality
caps, and cross-source deduplication.

### Upgrade 5 (this session): six reliability/ML hardening fixes

1. **History leakage fix** (`history.py`, `main.py`, `escalation.py`):
   `load_history(exclude_today=True)` drops rows scraped today before
   training. Previously the hourly scan (running at :05) would append
   today's listings to `history.csv`, then the 10:00 daily run would train
   on those same listings — a bargain would help define the average it was
   judged against, making it look ordinary. Now both daily and hourly use
   only pre-today rows as the training baseline, so execution order is
   irrelevant. Verified: first-day run correctly shows 0 training rows
   (all rows are today's), z-score fallback engages.

2. **Git push conflict fix** (`.github/workflows/daily.yml`,
   `.github/workflows/escalation.yml`): both workflows now share a single
   concurrency group `flat-searcher-state` so the second run queues instead
   of racing. The commit step is replaced with a pull-rebase-retry loop
   (5 attempts, backoff 10–50s) that fails the job loudly with `::error::`
   instead of silently swallowing push failures. `fetch-depth: 0` ensures
   `git pull --rebase` has the history it needs.

3. **Minimum-history escalation gate** (`escalation.py`, `config.py`):
   `ESCALATION_MIN_HISTORY=30` — escalation refuses to alert for a deal
   type until that type has ≥30 prior-day history rows. Without this, the
   first days could fire "HOT DEAL" emails off 7 data points. Verified:
   with 0 history rows, escalation logs "scores not yet trustworthy,
   escalation suppressed" and sends no alert. With 35 simulated rows and
   a clear bargain (score 3.6 ≥ 2.333), it would alert.

4. **Operator failure alerting** (`health.py`, `notifier.py`, `main.py`,
   `escalation.py`, `config.py`): detects three failure classes and emails
   the operator (not the recipient): `total_zero` (both scrapers dead),
   `source_zero:<src>` (one source broken while another works),
   `low_volume` (total < `MIN_EXPECTED_LISTINGS`). Throttled to once per
   issue per day via `data/ops_alerts.json`. Operator address =
   `OPS_EMAIL_TO` env, falling back to `EMAIL_FROM`. Verified: all three
   detection cases produce correct issue keys; healthy counts produce none.

5. **Ridge regression + cardinality caps** (`scoring.py`, `config.py`):
   plain least squares let rare one-hot categories (e.g. a city24
   development name covering a few listings) take extreme coefficients and
   memorise those rows, collapsing their residuals to ~0 so they could
   never be flagged as deals. Now: `RIDGE_LAMBDA=1.0` L2 penalty
   (intercept unpenalised), `MIN_CATEGORY_COUNT=5` buckets rare
   categories into `__other__`, `MAX_CATEGORIES_PER_FIELD=20` caps each
   field. Unseen categories at scoring time also fall into `__other__`.
   Verified: regression with 50 training rows + an unseen series name
   produces a sensible score (3.44) instead of crashing.

6. **Cross-source deduplication** (`utils.py`, `main.py`, `escalation.py`,
   `config.py`): the same flat is often on both ss.com and city24.lv under
   different IDs. `dedupe_cross_source()` merges clusters matching on
   (deal_type, district, rooms) with area within ±1.5 m² and price within
   ±3%, requiring a shared street token when both have street names.
   Survivor is chosen by `DEDUPE_SOURCE_PRIORITY` (ss.com first). Merged
   sources recorded on survivor as `also_on`. Verified: live run merged
   4 duplicates; survivor keeps ss.com URL with `also_on: ['city24.lv']`.

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
