# SERVICING — Flat_Searcher

Last updated: 2026-09-30 22:40

Living document. Updated after each Devin session. Read this first.

## Changelog

- 2026-09-30 22:40 — Session #6 (bug hunt): geocoder normalisation fix +
  failed-lookup cache fix, flat-digest HTML escaping, SHARE badge for
  co-ownership auctions, geocode coverage health check, '~' approximate
  distance marker. 102 tests. Details in the session section below.
- (earlier sessions predate the changelog — see the dated session
  sections below, newest first)

## Session 2026-09-30 #6 — geocoding bug (13% of flats had no position)

- ROOT CAUSE FOUND: SS.com's English pages render Latvian street names
  transliterated and abbreviated ("anninmuizhas 20" = Anniņmuižas iela
  20, "jurmalas g. 82/2" = Jūrmalas gatve 82 k-2, "kurzemes pr. 104a",
  "m. krūmu 18" = Mazā Krūmu iela, "imantas 16. l. 18" = Imantas 16.
  līnija, truncated "anninmuizhas stree.."). Nominatim matched none of
  them: 35 of 273 cached addresses had lat=None. Anniņmuižas/Augšzemes/
  Apūzes are 0.3–0.7 km from the school, so ~12 walking-distance flats
  were absent from the map, the "Walking distance" section AND got a
  neutral proximity score — exactly what Upgrade 11 was built to prevent.
- SECOND BUG: enrich_coordinates() skipped retries only when
  cached["tried_today"] == today, a key that was never written — every
  failed address was re-queried on every run (35 × 1.1 s daily, forever).
- FIX (geocode.py): address_candidates(addr) -> ordered [(query,
  precision)]: transliteration sh/zh/ch -> s/z/c (Nominatim accepts
  ASCII-folded names), abbreviations g./pr./l./b./d. + leading m. -> Mazā,
  "iela" appended when no street-type word, house-number variants
  (82/2 -> "82 k-2" -> "82"; 104a -> 104a -> 104), then the bare street
  as a 'street'-precision fallback. _plausible() rejects hits farther
  than GEOCODE_MAX_KM_FROM_SCHOOL (15 km) — Nominatim sometimes returns
  a same-named street in another town. The query no longer includes the
  canonical district name (it hurt more than helped). Cache entries now
  carry "precision"; misses are retried after GEOCODE_RETRY_FAILED_DAYS
  (30). Listings get geo_precision ('house'|'street'); street-level ones
  render distance as "~0.4 km" with a note, and the map popup says
  "street-level position".
- helper_scripts/regeocode_failed.py re-ran the fixed geocoder over the
  35 misses: 33 resolved (30 house-level, 3 street-level, 32 on the FIRST
  candidate). The 2 leftovers were izsoles keys with the "1/2 domājamā
  daļa no" prefix — obsolete now (see SHARE below), deleted from cache.
  Cache: 271 entries, 0 misses.
- health.py: new geocode_coverage issue when < GEOCODE_MIN_COVERAGE_PCT
  (85%) of listings have coordinates (main passes geocode.coverage()).
- SECURITY HARDENING (notifier.py): the flat digest interpolated
  district/street/floor/title/rooms/area and the ad url RAW into HTML in
  _main_row_html, _still_row_html, build_newest_html,
  build_near_school_html and build_auctions_html (the car digest and the
  budget embed were already escaped). Now: _t() html-escapes every
  scraped value; _source_link() uses utils.safe_url() (https:// only,
  no quotes/angle brackets) — a non-https url renders the source name
  without a link. geocode.get_map_data popups are escaped the same way
  and the marker JSON is "</"-escaped like the budget embed.
- NEW: co-ownership auctions. izsoles titles like "1/2 domājamā daļa no
  Višķu iela 11 - 5" sell only a FRACTION of a flat. izsoles.
  parse_ownership_share() -> listing["ownership_share"] ("1/2",
  "186/1000"; None = whole flat) and strips the prefix so the address
  geocodes. Auctions table shows a red "SHARE 1/2 — co-ownership, not a
  whole flat" badge; near-school rows and map popups flag it too.
- Housekeeping: deleted stray root files `nul` (Git-Bash 2>NUL artifact)
  and `%TEMP%cars_live.html` (1.2 MB probe dump). helper_scripts/ created
  (global rule 9).
- Tests: 102 (86 + 16 new in tests/test_flats_pipeline.py: candidate
  generation, fallback order + precision, far-hit rejection, cache retry
  policy, coverage/health, row/popup escaping + allow-list, SHARE badge,
  share parsing).
- GOTCHA found during verification: a truncated type word must be
  resolved, not dropped. "anninmuizhas boule.." first became
  "anninmuizas iela" (0.42 km) but the flats are on Anniņmuižas
  BULVĀRIS — a different street 1.09 km away (verified live). Now
  geocode._TRUNCATED_TYPES prefix-matches "boule"->bulvāris,
  "stree"->iela, "pros"->prospekts etc. Six Imanta flats moved from a
  wrong 0.42 km to ~1.09 km (street-level) — correctly OUTSIDE the 1 km
  walking radius.
- Verification: flat-only main.run() (cars.run stubbed) regenerated
  digest_2026-09-30: 136 listings, 0 without coordinates (was 87%
  coverage), walking-distance section 23 -> 28 flats within 1 km (new:
  Anniņmuižas 4/5/6/7/13/20, Augšzemes 5 at 0.30 km, Apūzes 51a, Imantas
  16. līnija 18 ...), 165 map markers (was 149). Same-day rerun side
  effect as in earlier sessions: last_digest date = today, so the
  "still active from yesterday" split is against the morning run.
- KNOWN/NEXT: (1) the first CI run after this change will spend a few
  extra Nominatim calls on any not-yet-cached addresses (1–3 requests
  each) — normal. (2) If SS.com changes its transliteration again, the
  geocode_coverage health line is the early warning; add the pattern to
  geocode._ABBREVIATIONS/_TRANSLIT and re-run helper_scripts/
  regeocode_failed.py. (3) Watch the "~" count in the near-school header
  — if it grows, house numbers stopped resolving.

## Session 2026-09-30 #5 — scrape-all caps removed + Market sub-tabs

- User asked to "scrape all" and let the budget input filter: the flat
  upper price cap (MAX_SALE_PRICE_EUR_EXCEPTIONAL = 85k) is REMOVED —
  main.py filters only on the MIN_SALE_PRICE_EUR floor (5k) for sale and
  rent; auctions likewise. config.py no longer defines
  MAX_SALE_PRICE_EUR_EXCEPTIONAL; MAX_SALE_PRICE_EUR (75k) remains as
  display-only "buyer's target budget".
- Flat embed cfg.maxPrice is now DYNAMIC (max row price, e.g. 385k
  today) — JSON-safe and self-adjusting. The budget tool's filter set is
  payload.rows + payload.extra (all 136 flats today), so custom budgets
  see every plausible flat, not just the top-N scored.
- Cars: CAR_COMPARABLE_MAX_PRICE_EUR raised 8k -> 1,000,000 — a
  plausibility bound only (keeps out exotics/mispriced ads, JSON-safe
  number for the embedded config). Effect today: eligible pool 2,348 ->
  4,589 ads; cars.html ~1.2MB (watch growth); market models 118 -> 144.
  Candidate ceiling stays CAR_PRICE_CEILING_EUR (5k) for the default
  view; the budget box explores above it.
- Market page (docs/market.html) now has Cars/Flats SUB-TABS
  (.mtab pill buttons, __mktTab JS, ?m=flats or #flats deep link).
  build_page renders whichever dataset(s) exist; empty side shows a
  "no stats yet" note.
- Side effects of unbounded flats: new-build exclusion now does the work
  the price cap did (70 excluded today vs 1 before); auctions grew to 28.
- 84 tests pass; new coverage for sub-tabs, dynamic embed max, and
  rows+extra budget coverage.

## Session 2026-09-30 #4 — flat Market section + flat budget filters

- flat_market.py: per-district stats (ads, first-seen-today count,
  median €/m², median ask, cheapest ad link) over today's in-budget sale
  listings; saved by main.run() to config.FLAT_MARKET_STATS_JSON
  (data/flat_market_stats.json) and rendered by car_market.build_page()
  as a second section on docs/market.html. District cells deep-link to
  index.html?district=X (the flat budget filter below).
- main.run() writes flat stats AFTER the empty-scrape early return —
  failed scans keep the last good stats; the flat section header shows
  its own "as of" date.
- Flat budget tool gained district + rooms selects
  (flat-filter-district / flat-filter-rooms), populated from the day's
  embedded rows; filters alone (no price) apply at the max ceiling;
  ?district=/?rooms= URL params; "5+" means >=5 rooms. The embed's rows
  are already capped at MAX_SALE_PRICE_EUR_EXCEPTIONAL, so the
  filters-only ceiling is cfg.maxPrice.
- cars.run(): pp.lv (Playwright, no internal retry) now gets one retry
  after 15 s on scrape failure; ss.com keeps its internal
  connection/timeout retry. Adds ~15 s to the failure-path test.
- car_market.build_page() renders when EITHER dataset exists; the
  placeholder ("Not generated yet") only when both are absent. Tests
  that assert the placeholder must patch flat_market.load_stats ->
  None (they did not before — real stats leaked in once flat stats
  existed).
- 82 tests pass. Known quirk: today's flat stats came from a fresh
  flat-only run (cars.run stubbed) — identical data path as the daily
  run.

## Session 2026-09-30 #3 — watchlist price deltas + Market tab

- Watchlist entries now flag price movement since starring: a bold
  green ▼ / red ▲ with the € delta ("▼ €1,200 since starred") appears
  next to still-listed items whose current price differs from the
  starred price. Pure JS change in CAR_WATCH_JS / FLAT_WATCH_JS —
  compares w0.price (snapshot at star time) with today's embedded price.
- New tab: docs/market.html ("Market"), rendered by website.build()
  from data/car_market_stats.json, which cars.run() writes each scan
  via car_market.compute_market_stats(deduped, qualified). Per
  make+model: ad count, median ask, cheapest ad (link), median year,
  median km, and how many of today's qualifying deals it has. Models
  with < CAR_MARKET_MIN_LISTINGS (3) ads are dropped as noise. Sortable
  columns, same mechanism as the digests. Not archived — a live view.
- cars.run() now calls car_market.save_stats(...); when the car scan
  fails entirely stats keep their previous date and website.build()
  shows the stale banner on the Market tab. Tests patch
  config.CAR_MARKET_STATS_JSON into the temp dir (added to _TempPaths).
- Note: today's stats were backfilled from the car digest's embedded
  market JSON (identical pool) instead of a 4th SS.com scrape.
- Later in the same session: model text-filter on Cars (normalized
  substring match, ?model= URL param); Market make/model cells link into
  cars.html?make=/\?model= filtered views; min-€ filter (?min=); badge
  summary line under the deals heading ("Today: N new · M still active");
  Comps < CAR_THIN_POOL_COMPS (8) rendered amber with "~" + tooltip —
  120/219 deals today are thin-pool, worth watching whether the marker
  is too liberal; and tests/test_flats_pipeline.py added — 12 unit tests
  for classify badge priority + scoring z-score fallback (the flats
  pipeline's first real coverage). 75 tests total, all passing.

## Session 2026-09-30 #2 — watchlist stars on both tabs

- Every deal row (cars: default table + custom-budget view; flats: top
  deals, still-active, near-school, custom-budget view) now has a ☆/★
  button. Clicking pins the listing to a collapsible "★ Watchlist (N)"
  box under the budget tool — stored in localStorage only
  (watch_cars_v1 / watch_flats_v1 keys), keyed by source:id, snapshotting
  label+price+url at star time. A watched listing absent from today's
  embedded data shows "NO LONGER LISTED — sold or expired" (red) with
  its last-seen price; present ones show today's price + still listed.
  ✕ removes an entry. Nothing leaves the browser.
- _FLAT_FIELDS gained "id" (needed for the flat watch key); the flat
  embed now also guards url to https:// only (was raw — pre-existing
  looseness fixed while passing).
- Micro-polish: sticky table headers (th position:sticky) + row hover
  on both tabs.
- JS hooks: CAR_WATCH_JS / FLAT_WATCH_JS constants; the budget re-render
  calls window.__carWatchRefresh/__flatWatchRefresh after rebuilding the
  custom table so fresh rows get correct star states. Both init on
  DOMContentLoaded like the budget tool.
- Tests: 57 pass incl. a Node run driving the real embedded JS through
  stub localStorage + click delegation (star live row + missing row,
  verifies stored keys, count, rendered rows).

## Session 2026-09-30 — SS.com ConnectTimeout in CI + nav-injection fix

- SYMPTOM: Flats tab showed the stale-digest banner; Cars tab showed
  "source outage". Cause: SS.com ConnectTimeout (network-level, 30 s
  connect timeout) from the GitHub Actions runner this morning — the
  flat scrape yielded 0 listings (city24 also empty) so main() took the
  "no flat digest rather than a fake one" path and website.build()
  stamped the stale banner; cars.run() saved a PP.lv-only digest with
  the outage box. SS.com was reachable locally minutes later — a
  transient window (or brief filtering of GH/Azure IPs), not a ban.
- FIX TODAY: full local main.run() regenerated digest_2026-09-30 +
  cars_2026-09-30 (219 deals, both sources) and pushed.
- HARDENING: ss_com._fetch and car_ss._fetch now retry connection/
  timeout errors twice with 10 s backoff (REQUEST_RETRIES /
  REQUEST_RETRY_DELAY_SECONDS in config.py). 403/429 still abort at
  once; HTTP errors are never retried.
- SEPARATE BUG FOUND 09-28 (user report "no tabs"): website._inject_nav
  regexed the first literal <body> — which also appears inside the
  budget JS comment in <head> — so nav landed inside the script element
  and never rendered. _real_body_tag() now skips <script>/<style>/<!--
  --> regions; JS comments no longer contain the literal. A regression
  test covers it (test_nav_ignores_body_text_inside_script).
- Windows gotcha seen this session: `2>NUL` in Git Bash creates a REAL
  file named NUL which then breaks `git add` (mmap Invalid argument).
  Use `2>/dev/null` in bash; `2>NUL` is cmd-only.
- ESP32 question answered: static files could technically be served off
  one, but the pipeline can't run there and Pages hosting is free —
  not sensible; any PC/Pi can self-host docs/ if ever wanted.

## Session 2026-09-28 #3 — car price history + filters

- car_seen.json entries now keep a `prices` trail ([date, price] per ask
  change, capped at CAR_PRICE_HISTORY_MAX_POINTS=60; a same-day re-run only
  updates today's point). Listings get `_first_seen`/`_price_hist`
  annotations; note score_and_rank returns COPIES (dict(listing)), so both
  `deduped` (market embed) and `assessed` (table rows) are annotated.
- The deals table and the custom-budget view now show "seen N d" (days
  since our scan first saw the ad — approx days listed), an "€a → €b"
  ask trail (hover = full dated history) and a tiny inline-SVG sparkline
  (red=dropping/green=rising/grey=flat). PRICE DROP semantics unchanged.
- Budget box gained filters: make (dropdown built from today's data),
  fuel, gearbox, min-year, max-km. They narrow CANDIDATES only — the
  comparable pools still cover the whole market, so medians/scores stay
  honest. Filters alone (no price typed) use the €5k default ceiling.
  URL params: ?max=&make=&fuel=&gearbox=&year=&km=.
- Market embed rows now carry _first_seen/_price_hist (_MARKET_FIELDS tail;
  url is sanitised by index, not position — do NOT go back to [:-1]+[url],
  that silently misaligned the row when fields were appended).
- Tests: 53 total; new ones cover trail accumulation, same-day updates,
  sparkline/history HTML, embed field order, and a Node run of the page's
  own JS proving filters narrow candidates correctly.

## Session 2026-09-28 #2 — in-browser custom-budget tool (both tabs)

- Both tabs now have a "Your budget" input: type a max price and the page
  recomputes instantly IN THE BROWSER — no rescraping, no backend, no
  secrets. Shareable via ?max=3500 (cars) / ?max=60000 (flats) URL param.
  Empty input / Reset = default daily view.
- CARS: the digest embeds the day's full eligible market (all prices up
  to the €8k comparable ceiling — not just today's €5k candidates) as
  compact JSON + the scoring config, and CAR_BUDGET_JS in car_digest.py
  ports car_value.score_and_rank to JS (eligibility, make/model/fuel
  grouping, tolerance matching, pool medians, 15%/€500/4-comp gate,
  condition-adjusted score). Budgets ABOVE €5,000 promote €5-8k cars from
  comparables to candidates and score them for the first time; below it,
  it is a pure filter. Custom view shows no badges/also_on links (state
  lives in car_seen.json, not embedded) — noted in the UI.
- PARITY GUARANTEE: tests/test_car_search.py::test_js_scoring_matches_
  python extracts the page's own JS + JSON, runs it under Node (skipped
  if node absent, e.g. CI) and asserts the same qualified set/scores/
  medians as Python. Known divergence: Math.round vs Python banker's
  rounding on exact .5 — tolerated at delta=1.
- FLATS: notifier.build_html embeds every scored listing (all_scored is
  now passed through main -> save_digest; the daily tables still cap at
  TOP_N_PER_TYPE) and FLAT_BUDGET_JS filters + re-renders them ranked by
  deal score. Pure filter — the regression does not depend on budget.
- Embed URLs are allow-listed at build time (same rule as _link());
  JSON is </ -escaped so it cannot break out of the script tag.
- Follow-up same day: added a blue OK button (plus Enter-key support) next
  to Reset after user feedback — Reset = clear budget / back to default
  daily view; OK/Enter applies immediately (typing still live-recomputes).
  Today's six generated files were surgically patched from the source
  constants (no 4th ss.com scan) — patch asserts one hit per file; the
  Node check confirmed the patched pages still compute 192 deals at 5k.
  Tomorrow's CI run regenerates everything from source as usual.
- Repo growth: the cars digest gains ~340 KB of embedded JSON per day
  (3 copies: data/digests + docs/cars.html + archive) ≈ +1 MB/day of
  git history. If that becomes a problem, the known lever is a
  string-table (source/make/model/... as dictionary indices) — would
  roughly halve it. The snapshot file stays numeric-only (no url) per
  its original contract; the embed builds its own rows from `market`.

## Session 2026-09-28 — sortable car columns + cron retune #2

- car_digest.py: the qualifying-deals table is now click-to-sort (same
  mechanism as the flats digest): all 7 headers toggle asc/desc, numeric
  columns sort via data-sort attributes (Listing falls back to make/model
  string compare), unknown values use -1 sentinel. A note under the h2
  says clicking sorts. Archive copies inherit it automatically (copies).
- test_escaping_and_url_allowlist updated: the digest now legitimately
  contains ONE template <script> (the sort JS); an injected listing title
  would make a second occurrence — count==1 is the assertion.
- Cron: 03:17 UTC slot started 09:12/09:42 UTC on 2026-09-27/28 (~6 h late,
  same as the old 06:23 slot) — GitHub's delay is roughly constant, not
  slot-dependent, so moved to `47 0 * * *` to land ~06:45 UTC = ~09:45
  Riga. CHECK THE ACTIONS TAB the morning after any retune.
- CI confirmed: the car scan runs fine in GitHub Actions (successful
  scheduled runs on 09-27 and 09-28; digests committed both days).

## Session 2026-09-26 #5 — "megaplan": audit + once-a-day, website-only cleanup

Audit findings (user asked whether the app is well built and whether two
tabs would exhaust GitHub scraping/Actions allowances):
- Actions budget: repo is PUBLIC -> Actions minutes on standard runners are
  free/unlimited. Real usage was 2.8-5.7 min/day; with the car scan ~15-20
  min/day. No quota risk. Only limits that matter: job timeout (now 45 min)
  and GitHub's 6 h job cap.
- Hourly scans: escalation.yml lost its cron on 2026-09-13 but all the
  machinery (escalation.py, alert email code, alerted_deals.json, config)
  was still present -> removed entirely this session.
- Cron lateness (the real scheduling problem): the 06:23 UTC slot started
  4.6-11.4 h late on every one of the last 22 scheduled runs (median ~5.5
  h; site refreshed ~15:00 Riga, not 10:00). Moved to `17 3 * * *` UTC.
  GitHub cron is best-effort; if it is still late, try another odd minute.
- Email: SMTP was never configured, so notifier.send, health ops-emails,
  the unsubscribe page and its PAT injection existed for a mailing list
  that does not exist. All removed -> the site now serves docs/ verbatim
  with NO secrets injected. UNSUBSCRIBE_PAT / TRIGGER_PAT repo secrets are
  unused; delete them in GitHub if not done already (TRIGGER_PAT: done).
- Dead code removed: exceptional.py (unused), history legacy wrappers
  (load_seen/save_seen/mark_seen/filter_new/migrate_seen_ids + unsubscribe/
  alerted/ops-alert helpers), SS_COM_MAX_PAGES_HOURLY, ESCALATION_*,
  EMAIL_*/SITE_URL/UNSUBSCRIBE_URL/OPS_EMAIL_TO, SEEN_IDS/UNSUBSCRIBED/
  ALERTED/OPS_ALERTS paths. notifier.py 1234 -> ~906 lines.
- Repo growth: each day added ~120 KB flat + ~100 KB car HTML stored twice
  plus ~1 MB of pretty-printed car JSON rewritten -> ~150-250 MB/yr of git
  history. Now: website.build() prunes digests older than
  config.ARCHIVE_KEEP_DAYS=30 from data/digests + docs/archive (newest of
  each kind always kept), and cars._write_json writes compact JSON.
- Frontend: static f-string HTML, inline styles, Leaflet from unpkg, no-JS
  tab nav. Works and is mobile-viewport tagged; styles are duplicated per
  archive page (~115 KB each). Not changed this session.
- Tests: 40 (38 car + main-zero-flats + 2 pruning). The flat pipeline
  (scoring/classify/dedupe/scrapers) still has NO unit tests — top
  follow-up if you touch it.

API surface changes: notifier.send() -> notifier.save_digest() returns
(path, info); health.check_and_alert() -> health.check() (log-only);
build_html() lost its `recipient` arg. README.md written (was "N/A").
daily.yml: no SMTP env, no token-injection step, timeout 45 min.
pages.yml renamed "Deploy site", plain upload of docs/.

## Session 2026-09-10 (system diagnostics, no repo changes)

No code changes this session. Session was used to diagnose why the PC
woke at 5 AM with fans spinning: Windows Update Orchestrator
("Schedule Wake To Work" task) woke the PC at 05:10, installed
KB5124008 (2026-09 cumulative, build 26200.9445) + KB5126052 (.NET),
auto-rebooted twice (~05:14-05:16). Fix options given to user:
disable WU wake via `HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU`
`AUPowerManagement=0` (targeted, keeps the user's 09:00
DailyTasksWakeAndShow wake task working) or disable all wake timers
via `powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 0`.
Note: user has a personal task `\DailyTasksWakeAndShow` (09:00 daily,
runs `C:\Users\rudol\CascadeProjects\work_stuff\DailyTasks.exe`) that
intentionally wakes the PC — don't disable it blindly.

Also ran a full "weird process" sweep same session: clean. No processes
outside C:\Windows / Program Files except Devin itself; network
connections all accounted for; non-Microsoft scheduled tasks all
identified (Acer OEM UpgradeTool, NitroSense fan control, Chrome
platform_experience_helper, user's own tasks). The `\SoftLanding\*`
COM-handler tasks that look fileless/malware-like are LEGIT Windows 11
content-delivery (tips/Spotlight) — CLSID resolves via
HKCR\PackagedCom to Microsoft-signed SoftLandingTask.exe in
SystemApps\MicrosoftWindows.Client.CBS; don't flag them next time.

## Session 2026-09-26 (car digest feature — cars on website only)

New feature: a daily car digest alongside the flat pipeline. Website only —
NO car email is ever sent. Buyer context: ~€1,200/mo income, provisional
€5,000 purchase cap (config.CAR_PRICE_CEILING_EUR) + separate €1,500 repair
reserve. Sources: ss.com cars (/lv/ only — robots.txt disallows /en/ and
sort URLs) + pp.lv (robots Allow:/, Crawl-delay 5 s -> Playwright, no cookie
clicks). auto24.lv / automoto.com.lv / autoplius.lt are NOT scraped (403s
and/or automation bans in their terms).

New files:
- `car_value.py` — canonical make/model (Passat B6/B7/B8, Golf 5/6/7 by
  year), eligible(), dedupe_cross_source(), score_and_rank() -> (ALL
  qualifying, assessed). A "good deal" = >=15% AND >=€500 below median of
  >=4 comparable current asking prices. DO NOT hand-edit: values verified
  by review of synthetic score/merge cases.
- `scrapers/car_ss.py` — scrape(max_pages_per_make=None, max_b7_pages=None).
  Make discovery: links matching /lv/transport/cars/<make>/sell/ on
  config.CAR_SS_MAKES_URL (48 makes live; /new/, /search/, /exchange/
  excluded). Pagination is <url>pageN.html — '?page=N' REPEATS page 1
  (verified live; never use it). Fetches /sell/ pages (<=2/make) +
  dedicated volkswagen/passat-b7 pages (<=4). Row = tr#tr_N, td.msg2 a.am,
  cells year|engine|mileage|price (msga2-o/msga2-r). '254 tūkst.' -> 254000,
  '254,5 tūkst.' -> 254500; '2.0D' -> diesel; '-' mileage -> None. Title
  specs matched on diacritic-stripped text ('automātiskā'->automatic,
  'mehāniskā'->manual, 'universāls'->wagon); buy/exchange/rent filter is
  anchored to the START of the title so 'Mainīta eļļa...' still parses.
  Stops cleanly on 403/429 (SourceBlocked), >=1 s between requests.
- `scrapers/car_pp.py` — scrape(max_pages=None), Playwright headless,
  config.CAR_PP_LIST_URL + &page=N (<=12), >=5 s between navigations.
  Cards a[href*='/!']; seller check via img alt 'Pārdod -'; specs in
  div[title='...'] tooltips ('Mehāniskā'->manual, 'Automātiskā'->automatic);
  price uses thin spaces (U+2009). Model = LAST path segment before !id
  (e.g. /bmw/x-serija/x1/!N -> x1). Page-1 zero cards -> RuntimeError.
- `cars.py` — run() -> status str. Per-source try/except; a source that
  raises OR yields zero eligible listings is marked failed for the run
  ('no eligible car listings' error). Both sources unavailable ->
  'No current data' error page and car_seen.json left untouched; one
  failed + one valid -> outage banner, digest still produced. Dedupe
  BEFORE scoring; writes data/car_market_snapshot.json (numeric-only
  fields from config.CAR_SNAPSHOT_FIELDS — the frozen deduped market the
  scores were computed on, overwritten daily, untouched on full failure);
  badges NEW / PRICE DROP (>=2%, wins at any age) /
  STILL ACTIVE (same price AND last_shown today|yesterday) / REAPPEARED;
  prunes seen >45 days.
- `car_digest.py` — build_html(qualified, assessed, source_counts,
  source_errors, badges, run_date). Header 'All qualifying deals' (no
  top-N limit). B7 watch = non-qualified assessed B7s sorted by
  |mileage - CAR_PASSAT_REFERENCE_MILEAGE_KM| then price, up to
  CAR_B7_WATCH_N; acquaintance reference note (config
  CAR_PASSAT_REFERENCE_PRICE_EUR/MILEAGE_KM, 'cannot appraise without
  exact specs') is shown even when the watch table is empty. €1,500
  reserve is for inspection/initial repairs/registration — fuel/tax/
  insurance/maintenance are additional recurring expenses. 'Warning:'
  text, no emoji. All values html.escape'd; links only to
  https://(www.)ss.com / (www.)pp.lv.

Changed:
- `website.py` — docs/cars.html (latest car digest or 'Not generated yet'
  placeholder). If the newest car digest is not today's, cars.html gets a
  prominent stale-warning banner naming the actual digest date (current
  tab only — archive copies stay clean). Car archive label is '(today)'
  only when it really is today, else '(latest)'. Flats/Cars tab bar
  (plain relative links, aria-current, no JS) injected ONLY into hosted
  docs/ copies — data/digests originals stay byte-identical. Idempotent
  (class="site-nav" marker).
- `main.py` — cars.run() once, wrapped (never aborts flats); website.build()
  also in the 0-flats early-return branch.
- `tests/test_car_search.py` — 35 unittests, all temp-dir writes.
  `python -m unittest discover -s tests -p "test_car_search.py" -v` -> OK.

LIVE RUN 2026-09-26 #2 (cars.run() + website.build() only, no flat scrape,
no email): ss.com 2532 -> 60 eligible, pp.lv 209 -> 92 eligible; 152
deduped market rows; 12 qualifying deals, 102 assessed. Regenerated
data/digests/cars_2026-09-26.html, car_seen.json, car_market_snapshot.json
(152 rows), docs/cars.html + archive copy. 35 unittests pass.
Parser fix applied this run: ss.com title-LPG override — ss.com:57095313
(2012 Passat B7, 222k km, €4,200, 'Benzīns + Gāze' in title) now correctly
stored as fuel='lpg' in the snapshot (was petrol). It is not displayed in
today's digest. The audited top B7 remains ss.com:57935290 (2011, 257k km,
€2,800, 17 peers median €5,200, 46.2% discount, score 100 — verified
against the frozen snapshot).
New this pass: inspection cautions on rows (>=300k km -> 'High mileage —
budget for repairs'; >=15y old -> 'Older car — inspect carefully'; no
score change), same-day rerun keeps NEW badges (first_shown field added
to car_seen state), docs/index.html gets a prominent stale-flat warning
when the newest flat digest is not today's (currently correctly warns the
flat digest is from 2026-09-13), and archive labels use '(latest)' instead
of a false '(today)'.
Earlier smoke: _discover_makes() -> 48 makes incl. volkswagen+skoda;
B7 pageN.html pages 1-2 -> 60 distinct ids.
User also shared a browser userscript that loads SS.com listing pages through
pageN.html. This confirms an option for wider coverage, but the current scraper
already uses pageN.html successfully; its 250 ms page delay and ?page=N
fallback should not be copied into the scheduled scan (which waits at least
1 second and stops on 403/429). The present limits are intentional caps, not a
pagination-loading failure.

Deployment 2026-09-26 #3 (same session): car feature committed and pushed
(9750d7f, rebased onto the day's remote data commits), together with a
security fix. Found the LIVE site was exposing a real fine-grained GitHub PAT
(github_pat_..., 93 chars) in public HTML: pages.yml/daily.yml/escalation.yml
sed-replaced __TRIGGER_TOKEN__ (and __UNSUBSCRIBE_TOKEN__ on the unsubscribe
page) into served pages. User chose: keep the unsubscribe mechanism, remove
the rescrape button. Done: notifier.py no longer emits the rescrape button/JS;
all three workflows lost the TRIGGER_TOKEN sed loop (unsubscribe sed kept);
34 existing digest files (data/digests + docs/archive, 2026-09-10..26) had the
button+script stripped; site rebuilt — index.html (flat 2026-09-26),
cars.html (cars_2026-09-26), archive.html (24 flat + 1 car digests), nav on
28 hosted pages. 35 tests pass, compileall clean.
USER ACTION REQUIRED: both old PAT values (TRIGGER_PAT, UNSUBSCRIBE_PAT) are
compromised — they were public. Delete the TRIGGER_PAT secret (nothing uses
it now); rotate UNSUBSCRIBE_PAT and update the repo secret to keep the
unsubscribe button working. Note the new value is again embedded in public
HTML by design (user-accepted tradeoff); a safe redesign needs a backend.
Verified live after deploy (0372ef5): cars.html serves 200 with the deals
table and tab nav; index/archives carry no btn-rescrape or TRIGGER_TOKEN;
unsubscribe page still injects its token (by design, pending rotation).

Follow-up 2026-09-26 #4 (model-blind car coverage + score rebalance): the
user wants best deals across ALL models — no model pointers. Removed every
Passat B7 special case (dedicated pages, B7 watch section, reference-mileage
config). scrapers/car_ss.py now: the per-make newest-pages scan tallies
(make, model) slugs seen in ad links, then deep-scans the top
CAR_SS_MAX_MODELS=200 models by observed volume, 2 pages each
(CAR_SS_MAX_PAGES_PER_MODEL). ss.com repeats page 1 for page numbers beyond
the last real page, so the existing zero-new-ids early stop is
load-bearing — do not remove it. car_value.score_and_rank now also scores
condition vs the comparable pool: CAR_SCORE_DISCOUNT_MULTIPLIER 2.0 -> 1.0
(+1 pt per 1% cheaper; deep discounts no longer saturate the scale at 25%),
up to +CAR_SCORE_MILEAGE_POINTS=10 for mileage below the pool median
(1 pt per 6k km across the +/-60k window) and +CAR_SCORE_YEAR_POINTS=3 per
year newer than the pool median. Pool median year/km is shown under
"Median ask". Live rerun: ss.com 8207 rows (492 models observed, 200
deep-scanned, 292 skipped as lower-volume), pp.lv 205; 177 qualifying
deals across 52 models (was 12, all B7). 38 tests pass.
WATCH: the ss.com scan is now ~500 requests (~9 min at the 1 s delay) and
the daily Actions job has a 25-min timeout including the flat pipeline —
if it starts timing out, lower CAR_SS_MAX_MODELS or
CAR_SS_MAX_PAGES_PER_MODEL.

Follow-up 2026-09-30 (cap removal + market sub-tabs + model rotation):
per the user, price ceilings are now "believable" bounds only. Flats:
MIN_SALE_PRICE_EUR=5000 floor stays; the €85k exceptional cap is gone
(removed from main.py's max_price map) — the embed now carries all
plausible flats and the budget tool searches rows+extra with a dynamic
ceiling = real data max. Cars: CAR_COMPARABLE_MAX_PRICE_EUR raised to
€1,000,000 (pure plausibility bound — must be a real number for the
browser scorer); eligible pool 2,348 → ~4,589 ads; market table covers
~144 models. Default car view still gates candidates at
CAR_PRICE_CEILING_EUR=5000 — that is the user's affordability setting,
NOT a scrape bound. market.html now has Cars/Flats sub-tabs (?m=flats /
#flats deep-links); each side renders independently when the other's
stats file is missing. The car custom-budget view now lists ALL matching
candidates (qualifying deals flagged) and reports "N qualifying of M
matching" — a model with zero underpriced listings still shows its
at-market listings instead of a bare 0. scrapers/car_ss.py deep-scan now
uses a persistent rotation state (data/car_model_scans.json,
make|model → last-scan ISO date): candidates = observed models + every
model in the make sidebars; ranking prefers never-scanned/stale models
(CAR_SS_MODEL_RESCAN_DAYS=4) so mid-volume models like passat-b7 get
covered on a cycle instead of starving under the volume ranking.
86 tests pass.

Known caveats: PP coverage = newest 12 pages, SS = newest 2 pages/make plus
a bounded deep scan of the 200 highest-volume model pages — deliberately
not exhaustive; the digest says so. Gearbox/body on ss.com are inferred
only when the title states them (else None = weaker comparables). Deep
"deals" on 250-360k km diesels dominate the top of the ranking because
that is where asking prices diverge most from pool medians — the
high-mileage caution rows flag this, and condition adjustments cannot
fully offset a 55%+ asking-price gap. First real run badged everything
NEW — PRICE DROP/STILL ACTIVE badges start meaning something from the
second run. docs/index.html refreshes on the next scheduled flat run, NOT
when cars run; cars are website-only (no car email). PP thumbnails are
blocked via route filter (image/media/font) — img alt seller labels still
parse since alt is markup, not a fetched resource.
The existing `main._inject_chat` copies the completion prompt to the clipboard
but does not submit it to Devin Desktop chat; this is not automatic injection.
A verified chat API or safely targeted Desktop input integration is needed;
blind keyboard simulation could send text to the wrong application. The
car-only verification ran `cars.run()` without invoking `main._inject_chat`.

## Current state (after session 2026-09-09, upgrade #12 — state auction integration)

**Working, tested end-to-end locally on Windows + Python 3.14.4.
The end recipient has reviewed the solution and is happy with it.**

Pipeline scope: SALES ONLY (DEAL_TYPES = ["sale"]). Three sources:
~196 SS.com + ~12 city24.lv regular listings + ~30 izsoles.ta.gov.lv
auctions daily. After filters typically ~86 regular listings +
~27 in-budget auctions. Regression model active on 230+ sale history rows.

### Upgrade 12 (this session): state/bailiff auction integration

**New source: izsoles.ta.gov.lv** (State Land Service e-auction site —
bailiff forced sales + state/municipal property; starting prices often
well below market).

**Scraping (scrapers/izsoles.py):**
- Site search is a POST form; the submit button `init-search=on` MUST be
  included or the server silently ignores all filters.
- Filters: ownership_type=owner (property rights), region=7 (Rīga),
  type=1 (real estate), category=3 (apartments).
- Pagination is path-based (/2, /3...) — filters are session-scoped,
  fetched on the same session. Riga apartments currently fit on 1 page.
- Detail pages (/izsole/{uuid}) have info-parameter/info-value div pairs:
  starting price, current bid, deposit, auction end date.
- Rooms/area live only inside the legal text. Two word orders occur:
  "platību 50,1 m2" and "45,56 m2 platībā" — both matched; area filtered
  to 10-500 m² to skip land parcels. Rooms matched as "2-istabu" digits
  or Latvian word forms ("divistabu"). Many announcements state neither
  (shown as "?"). Some detail pages have no description in HTML at all
  (likely PDF attachments) — rooms/area stay None.
- Ended auctions filtered out (end date < today).

**Display decisions (per site specifics):**
- Auctions get their OWN section, NOT mixed into the deal-score ranking:
  the regression model trains on regular sales and auction dynamics
  (bids, deadlines, deposits) are not comparable.
- Section: "State & bailiff auctions — Riga apartments", sorted by
  distance to school, capped at 15 rows with "+N more".
- Columns: Distance, Address, Rooms, m², Start price, Current bid,
  Appraisal (often unavailable — "-"), Ends, Source.
- Budget filter: current bid (or start price if no bids) must be within
  MIN_SALE_PRICE_EUR..MAX_SALE_PRICE_EUR_EXCEPTIONAL (3 dropped today).
- Purple (#8e44ad) markers on the map with auction popups
  (start price, current bid, end date).
- Auctions within 1 km of the school WOULD also appear in the
  walking-distance section (none today — closest is 1.32 km).
- Page header now lists izsoles.ta.gov.lv as a source.

**Where auctions are deliberately EXCLUDED:**
- Not in the regression training data or deal-score ranking.
- Not in history.csv / seen_deals / price_history (auction bids change
  daily but the auction lifecycle is weeks — daily snapshot is enough).
- Not in the hourly escalation scan (auctions move slowly; daily digest
  is sufficient).
- Not in cross-source dedupe (auction addresses are city-level "Riga",
  wouldn't match district-keyed dedupe anyway).
- Not in health.py source_counts (0 Riga auctions is legitimate).

**Config:** IZSOLES_ENABLED, IZSOLES_BASE, IZSOLES_TIMEOUT=30,
IZSOLES_DELAY=1.0, IZSOLES_MAX_PAGES=5, IZSOLES_MAX_DETAILS=30.

### Upgrade 11: near-school section + top-25 + map at top

**Problem found:** a 55K one-room flat at Lejina 6 (0.19 km from school)
never appeared in the digest. Root cause: its value score was -0.19 (55K
is market rate for a 1-room — comparable 1-rooms: 50K/30m², 53K/43m²,
55.5K/36m²), so blended score +0.81 fell below the old top-10 cutoff
(~+1.00). Structural bias: the value z-score compares within size class,
and larger flats achieve bigger absolute deviations, so the top-10 filled
with 75-81K 3-room flats. For the buyer's use case (kids need a place to
wait after school), "affordable + 200m away" is exactly right even when
it is not a statistical bargain.

**Fixes:**
1. "Walking distance to school" section: every in-budget listing within
   NEAR_SCHOOL_RADIUS_KM (1.0 km) of the school, sorted by distance,
   closest first. NO deal-score cutoff — guarantees near-school flats are
   always visible. Capped at NEAR_SCHOOL_MAX_ROWS (25) with "+N more".
   Columns: Distance, District, Street, Rooms, m², Floor, Price,
   EUR/m², Deal score, Source. Sortable. The 55K Lejina 6 flat now shows
   as row 9 (0.19 km).
2. TOP_N_PER_TYPE raised 10 -> 25 (main tables show more context).
3. Map moved to the TOP of the page (was at the bottom), right after
   the comparison header — first thing the recipient sees.
4. Street column added to the near-school section so buildings are
   distinguishable (Lejina 6 vs Lejina 22 are different buildings).

**Layout order now:** title/notes -> vs yesterday -> MAP ->
walking-distance section -> newest listings -> main tables (top 25) ->
still active -> footer.

**Schedule:** daily full digest now at 06:23 UTC = 09:23 Riga (moved
2026-09-10 from 07:00 UTC / 10:00 Riga — GitHub's round-hour 07:00 UTC
cron slot is the most contended of the day and the run was observed
delivering ~5h late, updating the site ~15:08-15:15 Riga on both
2026-09-09 and 2026-09-10, breaking the promised 10:00 update. The new
off-peak minute + 37-min buffer targets an on-time ~10:00 site update).
NOTE: GitHub cron remains best-effort — the digest header timestamp
(added 2026-09-10) always shows the true generation time, and the
Rescrape button lets anyone force a fresh run anytime.

**Hourly scan DISABLED (2026-09-13):** the hourly schedule was removed
from escalation.yml entirely. Rationale: without SMTP configured the
scan is pointless (its only output is the alert email), and even with
the Python-level SMTP guard (2026-09-09) each hourly run still burned
checkout + pip install + Playwright browser install (~3 min) plus an
identical Pages redeploy, 24x/day. The workflow is now manual-only
(workflow_dispatch); escalation.py keeps its SMTP guard as
defense-in-depth. TO RE-ENABLE hourly alerts when SMTP secrets are
configured: restore the schedule trigger in escalation.yml (commented
template at the top of the file, e.g. cron "13 * * * *" — off the :00/:05
rush). The daily digest and the Rescrape button are unaffected.

**Rescrape button + timestamp header (2026-09-10):**
- Digest header now shows date AND time in Riga timezone
  ("Riga flat deals - 2026-09-10 12:15 (Riga time)") via zoneinfo
  (tzdata added to requirements.txt for Windows).
- "Rescrape now" button on the site below the header: triggers the
  daily workflow via the GitHub API workflow_dispatch endpoint
  (POST /repos/{owner}/{repo}/actions/workflows/daily.yml/dispatches).
  Same pattern as the unsubscribe page: placeholders
  (__TRIGGER_TOKEN__/__REPO_OWNER__/__REPO_NAME__) are injected at
  deploy time by sed in ALL THREE workflows (pages.yml, daily.yml,
  escalation.yml) into docs/index.html and docs/archive/*.html.
- Token source: TRIGGER_PAT secret if set, falls back to
  UNSUBSCRIBE_PAT. If the button returns 403/422 the PAT lacks
  Actions-write permission — regenerate it with workflow dispatch
  rights or create TRIGGER_PAT.
- Client-side cooldown: 1 hour via localStorage (fs_last_trigger).
  NOTE: the token is visible in the public page source (same trust
  model as the unsubscribe button) and the cooldown is client-side
  only — anyone could extract it and trigger scrapes. Acceptable for
  this personal tool; the workflow's concurrency group serializes
  queued runs.
- In email clients the button is inert (no JS) — same as the map.
- Local/preview builds (placeholders not injected) show a "not
  available in this build" note instead.

**Hourly scan SMTP guard (2026-09-09):** the hourly escalation now
checks the SMTP env vars FIRST and skips the entire scan (exit 0, one
log line) when they are not configured. Rationale: without email the
scan produces no output at all — it only hammered ss.com/city24 ~24x/day
(IP-block risk) and its state commits raced with manual pushes, causing
workflow failures (seen 2026-09-09: the run failed on the git
pull-rebase step after data-file conflicts; the Python code itself ran
clean locally). The guard auto-enables hourly alerts the moment the
SMTP secrets are set in the repo. Daily digest is unaffected (it has a
no-SMTP fallback: saves the HTML digest).

### Upgrade 10: school proximity ranking + new-build exclusion

**Sales-only scope (end of session):**
- DEAL_TYPES = ["sale"] — rentals no longer scraped, scored, or shown.
- Digest has only sale tables; Distance column populated on every row.
- Runs faster (~100 fewer SS.com requests per scan).
- Header note: "Sales only - rentals are out of scope."
- Rent history rows remain in history.csv (unused, harmless).

**Known transient (not a bug):** city24.lv occasionally returns 0 listings
for one run (Playwright/network hiccup). The health check flags it as
source_zero:city24.lv, the digest still goes out on SS.com data, and the
next hourly scan recovers automatically. Verified working via probe.

### Upgrade 10: school proximity ranking + new-build exclusion

**School proximity (sale only):**
- Target school: Rīgas Ziemeļvalstu ģimnāzija, Paula Lejiņa iela 12, Zolitūde
  (lat 56.9464, lon 24.0207, geocoded once via Nominatim, hardcoded in config).
- Sale listings are ranked by a 50/50 blend:
  `final = 0.5 * deal_score + 0.5 * proximity_score`
- Proximity score: linear from +2.0 (next door) to -1.5 (3.5 km+ away).
  0 km → +2.0 ; 1 km → +1.0 ; 2 km → 0.0 ; 3 km → -1.0.
  Listings without coordinates get 0.0 (neutral, no penalty).
- Rent listings are ranked by deal score alone (unchanged).
- Implemented in scoring.py `score_and_rank()` — blend applied after
  regression/z-score, only when deal_type == "sale".
- geocode.py: `haversine_km()`, `distance_to_school()`, `proximity_score()`.
- main.py and escalation.py compute `_school_km` for every listing after
  geocoding (escalation.py now also geocodes, which it didn't before).

**Distance column in digest:**
- New sortable "Distance" column in all main + still-active tables
  (12 columns now, was 11).
- Sale rows show "0.8 km" (walking distance to school); rent rows show "-".
- Sortable via data-sort attribute (9999 = no coords, sorts last).

**School marker on map:**
- Red marker (radius 12, white border, "School" tooltip label) at the
  school's coordinates, inserted first in the marker list.
- Map header: "Map (124 listings + school)".
- Listed in geocode.py `get_school_marker()`.

**New-build exclusion:**
- SS.com marks new builds with series = "New" (87 listings in history).
- City24 checked for new-build keywords in series/title ("new project",
  "new development", "jaunprojekts", etc.).
- utils.py: `is_new_build()`, `filter_new_builds()` — used by both
  main.py and escalation.py.
- 36 new-build listings excluded in the 2026-09-08 run.

**Flexible price cap (sale):**
- MIN_SALE_PRICE_EUR = 5000 (floor, unchanged)
- MAX_SALE_PRICE_EUR = 75000 (target budget)
- MAX_SALE_PRICE_EUR_EXCEPTIONAL = 85000 (hard ceiling)
- Listings 75K-85K are kept — scoring ranks them naturally: a genuinely
  great 81K flat next to the school still surfaces; an overpriced 80K
  flat doesn't. The hard ceiling drops everything above 85K.
- 123 listings dropped by price filters in the 2026-09-08 run (mostly
  sales above 85K).

**Digest header notes:**
- Sale section subtitle explains the 50/50 blend.
- Page header note: "Sale ranking: 50% deal score + 50% walking distance
  to Rīgas Ziemeļvalstu ģimnāzija (shown in the Distance column). Rent
  ranking: deal score only. New builds excluded."

### Upgrade 9: daily rental detection + map coverage fix

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
