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
- **Archive** — the last 30 days of both digests.

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
| `data/seen_deals.json`, `data/last_digest.json` | NEW / PRICE DROP / STILL ACTIVE badges for flats |
| `data/price_history.json`, `data/geocode_cache.json` | price timelines, cached coordinates |
| `data/car_seen.json`, `data/car_market_snapshot.json` | car badges and the frozen comparable pool behind each score |
| `data/digests/` → `docs/` | generated HTML; pruned after `ARCHIVE_KEEP_DAYS` |

## Local run

```
pip install -r requirements.txt
python -m playwright install chromium
python -X utf8 -m main                       # full daily run (flats + cars + site)
python -X utf8 -c "import cars; cars.run()"  # cars only
python -X utf8 -c "import website; website.build()"
python -X utf8 -m unittest discover -s tests -v
```

All tunables live at the top of `config.py`. `SERVICING.md` is the living
maintenance log — read it first when picking the project up.
