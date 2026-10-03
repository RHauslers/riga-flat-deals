# riga-flat-deals

A once-a-day scraper that ranks Riga flat listings and affordable used cars
by how cheap they are compared with comparable current asking prices, and
publishes the result as a static site on GitHub Pages:

- **Flats** — sale listings in Zolitude / Sampeteris / Imanta from ss.com and
  city24.lv (plus state/bailiff auctions from izsoles.ta.gov.lv), scored by a
  ridge regression trained on the accumulated listing history and blended
  with walking distance to the target school.
- **Cars** — ss.com + pp.lv seller ads up to €8,000, deduplicated across the
  two sites, with every car scored against comparable ads of the same
  model/fuel/year/mileage/engine; low mileage and newer year lift the score.
- **Trends & gone** — per-district price trends (7-day delta + sparkline),
  price sparklines on each flat row, auction countdown badges, and a
  "recently gone" section for sold/delisted flats and cars.
- **Archive** — the last 30 days of both digests. Archived car pages have
  the large embedded market JSON stripped (the budget tool is a live-page
  feature); tables and gone sections stay intact.

Website only: nothing is emailed, there are no hourly scans, and the site
holds no credentials.

## How it runs

`.github/workflows/daily.yml` runs `python -X utf8 -m main` once a day
(cron `17 3 * * *` UTC — GitHub cron is best-effort and usually starts late),
commits the updated `data/` + `docs/` back to `main`, and deploys `docs/` to
Pages. `pages.yml` redeploys the site when a human pushes site changes.

State lives in the repo so it survives between runs:

| file | purpose |
|---|---|
| `data/history.csv` | every flat listing ever seen — training data |
| `data/seen_deals.json`, `data/last_digest.json` | NEW / PRICE DROP / STILL ACTIVE badges + gone tracking for flats |
| `data/price_history.json`, `data/geocode_cache.json` | price timelines (row sparklines), cached coordinates |
| `data/flat_market_history.json`, `data/car_market_history.json` | per-district / per-model median-price history for trend Δ7d + sparklines |
| `data/car_seen.json` | car badges + gone tracking — v2 compact format (`v2` keys, epoch days); v1 files migrate on read |
| `data/car_market_snapshot.json` | frozen comparable pool — v2 columnar format (`{v, date, fields, rows}`); v1 files migrate on read |
| `data/car_market_stats.json` | per-model market stats behind the browser-side budget scorer |
| `data/digests/` → `docs/` | generated HTML; pruned after `ARCHIVE_KEEP_DAYS` |

`helper_scripts/compact_state.py` backs up and rewrites the legacy v1 state
files to the v2 formats (safe to run repeatedly; v1 files also migrate
transparently on first read). The embedded market JSON inside the digests is
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
