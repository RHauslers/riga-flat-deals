# riga-flat-deals

A once-a-day scraper that ranks Riga flat listings and affordable used cars
by how cheap they are compared with comparable current asking prices, and
publishes the result as a static site on GitHub Pages:

- **Flats** — sale listings in Zolitude / Sampeteris / Imanta from ss.com and
  city24.lv (plus state/bailiff auctions from izsoles.ta.gov.lv), scored by a
  ridge regression trained on the accumulated listing history and blended
  with walking distance to the target school.
- **Cars** — ss.com + pp.lv seller ads (candidate ceiling €5,000; the wider
  comparable pool goes up to ~€1M for context), deduplicated across the two
  sites, with every car scored against comparable ads of the same
  model/fuel/year/mileage/engine; low mileage and newer year lift the score.
- **Trends & gone** — per-district / per-model price trends (7-day delta +
  sparkline), price sparklines on each flat row, auction countdown and
  registration-deadline badges plus NEW / bid-movement markers, and a
  "recently gone" section for sold/delisted flats, cars and ended auctions.
- **Archive** — the last 30 days of both digests. Archived digest pages have
  the large embedded market JSON stripped (the budget tool is a live-page
  feature); tables and gone sections stay intact. The archive index also
  shows a 14-day coverage strip so missed runs are visible at a glance.

Website only: the pipeline runs once each morning via the scheduled GitHub
Actions job, and the site holds no credentials.

## How it runs

`.github/workflows/daily.yml` runs the unit suite, then
`python -X utf8 -m main` once a day (cron `47 0 * * *` UTC — GitHub cron
is best-effort and usually starts hours late; the early slot lands the
actual run ~09:45 Riga time), commits the updated `data/` + `docs/` back
to `main`, and deploys `docs/` to Pages. `pages.yml` redeploys the site
when a human pushes site changes. A failing test aborts the run before
any state writes. Locally, `data/.main.lock` prevents two simultaneous
`main` runs from corrupting `docs/` (a stale >4 h lock is auto-cleared).

State lives in the repo so it survives between runs:

| file | purpose |
|---|---|
| `data/history.csv` | every flat listing ever seen — training data |
| `data/seen_deals.json`, `data/last_digest.json` | NEW / PRICE DROP / STILL ACTIVE badges + gone tracking for flats |
| `data/price_history.json`, `data/geocode_cache.json` | price timelines (row sparklines), cached coordinates |
| `data/flat_market_history.json`, `data/car_market_history.json` | per-district / per-model median-price history for trend Δ7d + sparklines |
| `data/car_seen.json` | car badges + gone tracking — v2 compact format (`v2` keys, epoch days); v1 files migrate on read |
| `data/car_market_snapshot.json` | frozen comparable pool — v2 columnar format (`{v, date, fields, rows}`); v1 files migrate on read |
| `data/car_market_stats.json`, `data/flat_market_stats.json` | per-model / per-district market stats behind the Market page and the browser-side budget scorer |
| `data/flat_active.json` | yesterday's live flat + auction ads for gone/ended detection |
| `data/digests/` → `docs/` | generated HTML; pruned after `ARCHIVE_KEEP_DAYS` |

`helper_scripts/compact_state.py` backs up and rewrites the legacy v1 state
files to the v2 formats (safe to run repeatedly; v1 files also migrate
transparently on first read). `helper_scripts/backfill_flat_market.py`
seeds `flat_market_history.json` from `history.csv`, and
`helper_scripts/audit_data.py` prints a health report over every state
file (including lock leftovers, series ordering and digest embeds). All
JSON state writes go through an atomic tmp+replace, so a crash mid-write
can't truncate state. The embedded market JSON inside the digests is
dictionary-encoded and decoded by the page's own JavaScript.

## Local run

```
pip install -r requirements.txt
python -m playwright install chromium
python -X utf8 -m main                       # full daily run (flats + cars + site)
python -X utf8 -c "import cars; cars.run()"  # cars only
python -X utf8 -c "import website; website.build()"
python -X utf8 -m unittest discover -s tests -v
```

`.github/workflows/tests.yml` runs the same unittest discover command on
pushes to `main` and pull requests (scraper tests are fully mocked — no
network or Playwright browser needed). `tests/test_parsers.py` pins each
parser's expected output against saved fixtures in `tests/fixtures/`, so a
site markup change shows up as a parser test failure before the daily run
silently finds nothing.

All tunables live at the top of `config.py`. `SERVICING.md` is the living
maintenance log — read it first when picking the project up.
