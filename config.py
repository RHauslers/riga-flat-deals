# -*- coding: utf-8 -*-
"""
Flat_Searcher - configuration.
All hard-coded variables live at the top of this file (per project rules).
Change values here only; every other module imports from config.
"""
import os

# ----------------------------------------------------------------------------
# 1. TARGET DISTRICTS (Riga, Latvia)
#    Each entry: canonical name -> list of substrings used to match the
#    district inside scraped listing text (case-insensitive). ss.com lists
#    Sampeteris as "Shampeteris-Pleskodale"; city24.lv uses "Riga, Sampeteris".
# ----------------------------------------------------------------------------
DISTRICTS = {
    "Zolitude":  ["zolitude", "zolitūde"],
    "Sampeteris": ["sampeteris", "shampeteris", "šampēteris", "sampēteris"],
    "Imanta":    ["imanta", "imantas"],
}

# ----------------------------------------------------------------------------
# 2. DEAL TYPES  ("rent" and/or "sale")
# ----------------------------------------------------------------------------
DEAL_TYPES = ["rent", "sale"]

# ----------------------------------------------------------------------------
# 3. ss.com settings
#    English "today" pages for Riga flats. One page holds all of today's ads.
# ----------------------------------------------------------------------------
SS_COM_BASE = "https://www.ss.com"
SS_COM_TODAY_URL = {
    # ss.com calls rent "hand_over" (landlord hands the flat over).
    "rent": "https://www.ss.com/en/real-estate/flats/riga/today/hand_over/",
    "sale": "https://www.ss.com/en/real-estate/flats/riga/today/sell/",
}
# District-specific listing pages (not just "today" — shows ALL active listings).
# This is where CenuMednieks historical data is most valuable: older listings
# that have been on the market for weeks/months.
SS_COM_DISTRICT_SLUGS = {
    "Zolitude": "zolitude",
    "Sampeteris": "shampeteris-pleskodale",
    "Imanta": "imanta",
}
SS_COM_DEAL_SLUGS = {
    "rent": "hand_over",
    "sale": "sell",
}
SS_COM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SS_COM_TIMEOUT = 30   # seconds per request
SS_COM_MAX_PAGES = 15  # safety cap for pagination (Imanta sale has 9+ pages)
SS_COM_MAX_PAGES_HOURLY = 3  # hourly scan uses fewer pages to avoid IP blocks

# ----------------------------------------------------------------------------
# 4. city24.lv settings (scraped via Playwright -> intercept JSON API)
#    The site is a JS SPA that calls api.city24.lv with an anti-bot token.
#    Playwright renders the page in a real browser (token handled for us) and
#    we intercept the JSON search responses.
# ----------------------------------------------------------------------------
CITY24_ENABLED = True
CITY24_SEARCH_URL = {
    "rent": "https://www.city24.lv/real-estate-search/apartments-for-rent",
    "sale": "https://www.city24.lv/real-estate-search/apartments-for-sale",
}
CITY24_MAX_PAGES = 8        # pages to walk per deal type (each ~20 listings)
CITY24_NAV_TIMEOUT = 45000  # ms
CITY24_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ----------------------------------------------------------------------------
# 5. SCORING / ML settings
#    Linear regression: price ~ rooms + area + floor + floor_total
#                         + district(one-hot) + series(one-hot) + source(one-hot)
#    Retrained every run on the full history. A listing is a "great deal" when
#    its actual price is far BELOW the model's prediction (big negative residual).
#    deal_score = -(residual) / std(training residuals)  -> z-score, higher=better.
#    Falls back to a per-bucket €/m² z-score when history is too small.
# ----------------------------------------------------------------------------
MIN_TRAIN_ROWS = 40        # below this -> use z-score fallback
TOP_N_PER_TYPE = 10        # how many best deals to show per deal type in email
PRICE_OUTLIER_Z = 4.0      # drop training rows whose price is > 4 std from mean

# Ridge regularisation. Plain least squares lets a rare one-hot category (e.g.
# a city24 development name covering 11 listings) take an extreme coefficient
# and memorise those rows, collapsing their residuals to ~0 so they can never
# look like deals. A small L2 penalty prevents that. Intercept is not penalised.
RIDGE_LAMBDA = 1.0

# Categorical cardinality caps (protects against unbounded one-hot growth).
# city24's "series" is really a free-text project/development name, so its
# cardinality grows forever as new developments launch. Categories seen fewer
# than MIN_CATEGORY_COUNT times are bucketed into "__other__", and at most
# MAX_CATEGORIES_PER_FIELD (most frequent) are kept per field.
MIN_CATEGORY_COUNT = 5
MAX_CATEGORIES_PER_FIELD = 20

# Sanity filters: drop listings with implausible prices (city24 occasionally
# returns garbage like 189 EUR for a sale listing). These are minimums only.
MIN_SALE_PRICE_EUR = 5000    # below this, a sale listing is likely erroneous
MIN_RENT_PRICE_EUR = 50      # below this, a rent listing is likely erroneous

# 5b. ESCALATION (hourly hot-deal alerts)
#    A lightweight hourly scan scores all current listings. If any deal's
#    score >= ESCALATION_SCORE_THRESHOLD and it hasn't been alerted yet, an
#    instant alert email is sent (separate from the daily digest). The daily
#    digest is unaffected. Alerted deals are tracked in alerted_deals.json to
#    avoid re-alerting the same listing every hour.
ESCALATION_ENABLED = True
ESCALATION_SCORE_THRESHOLD = 2.333  # ~1-in-100 statistical outlier (user-chosen)
# A z-score computed from a handful of rows is noise, not signal. Escalation
# refuses to alert for a deal type until that type has this many history rows,
# so the first days can't produce false "HOT DEAL" emails.
ESCALATION_MIN_HISTORY = 30

# ----------------------------------------------------------------------------
# 5c. CROSS-SOURCE DEDUPLICATION
#    The same flat is often listed on both ss.com and city24.lv under different
#    IDs. Without dedup the recipient sees it twice AND it is double-counted in
#    the training data. Two listings are treated as the same flat when they
#    share (deal_type, district, rooms) and their area/price are within these
#    tolerances.
# ----------------------------------------------------------------------------
DEDUPE_ENABLED = True
DEDUPE_AREA_TOL_M2 = 1.5     # areas within +/- 1.5 m2 count as equal
DEDUPE_PRICE_TOL_PCT = 3.0   # prices within +/- 3% count as equal
DEDUPE_SOURCE_PRIORITY = ["ss.com", "city24.lv"]  # which listing to keep

# ----------------------------------------------------------------------------
# 5d. HEALTH / FAILURE ALERTING (to the OPERATOR, not the recipient)
#    Scrapers die silently when a site is redesigned: 0 listings -> no email ->
#    nobody notices for days. If a run looks unhealthy we email the operator.
#    Operator address comes from OPS_EMAIL_TO, falling back to EMAIL_FROM.
#    Alerts are throttled to once per issue per day.
# ----------------------------------------------------------------------------
HEALTH_ALERTS_ENABLED = True
MIN_EXPECTED_LISTINGS = 50    # total below this (but > 0) = suspicious
ALERT_ON_SOURCE_ZERO = True  # a source returning 0 while another returns > 0
# Operator alert recipient. Falls back to EMAIL_FROM if not set.
OPS_EMAIL_TO = os.environ.get("OPS_EMAIL_TO", "")

# ----------------------------------------------------------------------------
# 5e. PRICE HISTORY (CenuMednieks.lv + our own daily tracking)
#    CenuMednieks.lv tracks SS.lv ad price history — original price, changes,
#    days on market. We use it to backfill history we missed before our first
#    run. Our own daily tracking supplements this going forward.
#    Only SS.com listings can be enriched (CenuMednieks tracks SS.lv only).
# ----------------------------------------------------------------------------
PRICE_HISTORY_CENU_ENABLED = True
CENU_REFRESH_DAYS = 7       # re-fetch CenuMednieks data weekly (not daily)

# ----------------------------------------------------------------------------
# 5f. GEOCODING + MAP
#    City24.lv API returns lat/lon directly. SS.com listings are geocoded
#    via Nominatim (free OSM geocoder, no API key, 1 req/sec).
#    Results cached in data/geocode_cache.json (streets don't move).
# ----------------------------------------------------------------------------
GEOCODE_ENABLED = True
MAP_ENABLED = True

# ----------------------------------------------------------------------------
# 6. EMAIL DIGEST settings (env-ready: reads from environment / GitHub secrets)
#    Set these secrets in GitHub Actions (or your local env) to enable sending:
#       SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
#    If any are missing, the digest is written to data/digest_YYYY-MM-DD.html
#    and a notice is printed instead of crashing.
# ----------------------------------------------------------------------------
EMAIL_SUBJECT_PREFIX = "Riga flat deals"
EMAIL_LANG = "en"  # language of the digest body

# 6b. UNSUBSCRIBE settings
#    Set UNSUBSCRIBE_URL as an env var / GitHub secret once the GitHub Pages
#    unsubscribe page is deployed, e.g.
#    "https://yourname.github.io/Flat_Searcher/unsubscribe.html"
#    The recipient's email is appended as ?email=<recipient> automatically.
UNSUBSCRIBE_URL = os.environ.get("UNSUBSCRIBE_URL", "")  # set as env/secret once Pages is live

# 6b2. HOSTED SITE URL (optional)
#    Set SITE_URL as an env var / GitHub secret once GitHub Pages is live, e.g.
#    "https://yourname.github.io/Flat_Searcher"
#    Adds a "View in browser" link at the top of the email + links in the digest.
SITE_URL = os.environ.get("SITE_URL", "")

# 6c. DEAL PERSISTENCE settings
PRICE_DROP_MIN_PCT = 2.0    # only badge as PRICE_DROP if price dropped >= 2%
STILL_ACTIVE_MAX_DAYS = 7   # don't show "still active" for deals shown > N days ago

# ----------------------------------------------------------------------------
# 7. CHAT INJECTION (global rule 4)
#    After a local run the script copies a status prompt to the clipboard so it
#    can be pasted into the Cascade/Devin chat. Set to False to disable.
# ----------------------------------------------------------------------------
CHAT_INJECT_ENABLED = True

# ----------------------------------------------------------------------------
# 8. FILE PATHS (data dir is committed so history persists across CI runs)
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
SEEN_IDS_JSON = os.path.join(DATA_DIR, "seen_ids.json")  # legacy (migrated)
SEEN_DEALS_JSON = os.path.join(DATA_DIR, "seen_deals.json")
LAST_DIGEST_JSON = os.path.join(DATA_DIR, "last_digest.json")
UNSUBSCRIBED_JSON = os.path.join(DATA_DIR, "unsubscribed.json")
ALERTED_DEALS_JSON = os.path.join(DATA_DIR, "alerted_deals.json")
OPS_ALERTS_JSON = os.path.join(DATA_DIR, "ops_alerts.json")
PRICE_HISTORY_JSON = os.path.join(DATA_DIR, "price_history.json")
GEOCODE_CACHE_JSON = os.path.join(DATA_DIR, "geocode_cache.json")
DIGEST_DIR = os.path.join(DATA_DIR, "digests")
HISTORY_COLUMNS = [
    "scrape_date", "source", "deal_type", "id", "url", "district", "street",
    "rooms", "area_m2", "floor", "floor_num", "floor_total", "series",
    "price_eur", "price_per_m2", "title",
    "ad_slug", "old_price", "show_price_drop", "price_unit",
]
