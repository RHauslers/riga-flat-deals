# -*- coding: utf-8 -*-
"""
Flat_Searcher - configuration.
All hard-coded variables live at the top of this file (per project rules).
Change values here only; every other module imports from config.
"""
import os

CAR_SS_BASE = "https://www.ss.com"
CAR_SS_MAKES_URL = "https://www.ss.com/lv/transport/cars/sell/"
CAR_SS_MAX_PAGES_PER_MAKE = 2
CAR_SS_MAX_PAGES_PER_MODEL = 2
CAR_SS_MAX_MODELS = 200
# Deep-scan backlog rotation: a model page whose last deep-scan is older
# than this (or never happened) jumps the deep-scan queue, because its
# backlog ads are unreachable from the make's newest-ads pages. Fresh
# ads still arrive via the make scan regardless.
CAR_SS_MODEL_RESCAN_DAYS = 4
CAR_SS_REQUEST_DELAY_SECONDS = 1.0
CAR_PP_LIST_URL = "https://pp.lv/lv/transports-un-tehnika/vieglie-auto/?maxPrice=8%C2%A0000"
CAR_PP_MAX_PAGES = 12
CAR_PP_REQUEST_DELAY_SECONDS = 5.0
CAR_SOURCE_TIMEOUT_SECONDS = 30
# Transient-network resilience (2026-09-30: an SS.com ConnectTimeout window
# killed the whole flat scrape from the CI runner). Retries apply ONLY to
# connection/timeout errors and the statuses in SS_COM_RETRY_STATUS;
# 403 still aborts immediately.
REQUEST_RETRIES = 2            # extra attempts per request on conn errors
REQUEST_RETRY_DELAY_SECONDS = 10
# HTTP statuses worth one bounded retry (rate-limit / transient server
# errors). Each retry sleeps Retry-After, or RETRY_DELAY*(attempt+2).
SS_COM_RETRY_STATUS = (429, 500, 502, 503, 504)
CAR_MIN_YEAR = 2005
CAR_MAX_MILEAGE_KM = 400000
# Fuel values a listing must carry to be eligible — checked by both
# car_value.eligible() and cars._ineligible_reason().
CAR_FUEL_TYPES = ("petrol", "diesel", "hybrid", "electric", "lpg")
CAR_HIGH_MILEAGE_WARNING_KM = 300000
CAR_AGE_WARNING_YEARS = 15
CAR_MIN_PRICE_EUR = 1000
CAR_PRICE_CEILING_EUR = 5000
# Upper bound on the embedded market pool — a plausibility bound only
# (exotics/mispriced ads above ~1M EUR are noise), not a budget filter:
# the browser budget tool narrows by the user's own number.
CAR_COMPARABLE_MAX_PRICE_EUR = 1000000
CAR_REPAIR_RESERVE_EUR = 1500
CAR_MIN_COMPARABLES = 4
CAR_YEAR_TOLERANCE = 2
CAR_MILEAGE_TOLERANCE_KM = 60000
CAR_ENGINE_TOLERANCE_L = 0.15
CAR_GOOD_MIN_DISCOUNT_PCT = 15.0
CAR_GOOD_MIN_SAVINGS_EUR = 500
CAR_DEDUPE_MILEAGE_TOLERANCE_KM = 500
CAR_DEDUPE_PRICE_TOLERANCE_EUR = 50
CAR_DEDUPE_ENGINE_TOLERANCE_L = 0.06
CAR_DEDUPE_MISSING_SPECS_MILEAGE_TOLERANCE_KM = 250
CAR_SCORE_CENTER = 50
CAR_SCORE_DISCOUNT_MULTIPLIER = 1.0
CAR_SCORE_MAX = 100
CAR_SCORE_MILEAGE_POINTS = 10
CAR_SCORE_YEAR_POINTS = 3
CAR_SEEN_TTL_DAYS = 45
CAR_PRICE_HISTORY_MAX_POINTS = 60  # [date, price] points kept per listing
# Below this many comparable ads the pool median is thin evidence; the
# digest marks such comps counts with a "~" warning.
CAR_THIN_POOL_COMPS = 8
CAR_SNAPSHOT_FIELDS = ("source", "id", "make", "model", "year", "mileage_km",
                       "fuel", "engine_l", "gearbox", "body", "price_eur",
                       "url")
# Market tab (docs/market.html): per-model stats over the whole eligible
# pool. Models with fewer ads than this are omitted as noise.
CAR_MARKET_MIN_LISTINGS = 3

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
# 2. DEAL TYPES  — sales only. The buyer is purchasing a flat near the school
#    for their daughters; rentals are out of scope.
# ----------------------------------------------------------------------------
DEAL_TYPES = ["sale"]

# ----------------------------------------------------------------------------
# 3. ss.com settings
#    Per-district listing pages for Riga flats (all active ads — the old
#    "today" page was redundant).
# ----------------------------------------------------------------------------
SS_COM_BASE = "https://www.ss.com"
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
# Politeness gap between flat-page fetches — the car scrape already enforces
# 1 s/request; the flat district pages fired ~40 requests back-to-back
# (fixed 2026-10-03, 429 resilience).
SS_COM_REQUEST_DELAY_SECONDS = 0.5

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
TOP_N_PER_TYPE = 25        # how many best deals to show per deal type in the digest
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
# (MAX_SALE_PRICE_EUR removed 2026-10-04 — nothing displayed it; the
#  budget lives in the digest's budget input.)
# No upper sale cap: every plausible listing is kept — the browser budget
# tool filters by the user's own maximum instead.
MIN_RENT_PRICE_EUR = 50      # below this, a rent listing is likely erroneous

# ----------------------------------------------------------------------------
# 5a. SCHOOL PROXIMITY (Rīgas Ziemeļvalstu ģimnāzija)
#      The buyer's daughters attend this school. Sale listings are ranked by
#      a 50/50 blend of deal score (cheap vs model) and proximity (walking
#      distance to the school). Rent listings are unaffected.
# ----------------------------------------------------------------------------
SCHOOL_NAME = "Rīgas Ziemeļvalstu ģimnāzija"
SCHOOL_ADDRESS = "Paula Lejiņa iela 12, Zolitūde, Riga"
SCHOOL_LAT = 56.9464128
SCHOOL_LON = 24.0207296
# Proximity score: linear from +2.0 (next door) to -1.5 (far edge).
# 0km -> +2.0 ; 1km -> +1.0 ; 2km -> 0.0 ; 3km -> -1.0 ; 3.5km+ -> -1.5
PROXIMITY_WEIGHT = 0.5      # blend weight (0.5 = 50% deal score + 50% proximity)
PROXIMITY_MAX_KM = 3.5      # beyond this, proximity penalty floors at -1.5

# "Walking distance to school" section: every in-budget listing within this
# radius is listed (sorted by distance) so that fairly-priced flats near the
# school are never hidden by the bargain ranking.
NEAR_SCHOOL_RADIUS_KM = 1.0
NEAR_SCHOOL_MAX_ROWS = 25   # cap the section; "+N more" note beyond this

# ----------------------------------------------------------------------------
# 5b. NEW BUILD EXCLUSION
#      The buyer explicitly does not want newly built apartments. SS.com marks
#      these with series = "New". City24's project names are free text, so we
#      also check for common new-build keywords there.
# ----------------------------------------------------------------------------
EXCLUDE_NEW_BUILDS = True
NEW_BUILD_SERIES = ["new"]  # lowercase SS.com series values to exclude
NEW_BUILD_KEYWORDS = [      # keywords in city24 series/title to exclude
    "new project", "new development", "jaunprojekts", "jaunā projekta",
]

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
# 5d. HEALTH CHECKS (log-only)
#    Scrapers die silently when a site is redesigned: 0 listings -> nobody
#    notices for days. Suspicious counts are printed as [health] ISSUE lines
#    in the Actions log, and website.build() shows a stale-digest banner.
# ----------------------------------------------------------------------------
MIN_EXPECTED_LISTINGS = 50    # total below this (but > 0) = suspicious
ALERT_ON_SOURCE_ZERO = True  # a source returning 0 while another returns > 0

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
# Nominatim sometimes matches a same-named street in another town; every
# target district lies within ~5 km of the school, so anything farther than
# this is a wrong hit and is discarded (the listing stays un-geocoded).
GEOCODE_MAX_KM_FROM_SCHOOL = 15.0
# A failed lookup is cached and retried only after this many days (SS.com
# street text is stable; hammering Nominatim daily with the same miss is
# pointless). 2026-09-30: failures used to be retried EVERY run.
GEOCODE_RETRY_FAILED_DAYS = 30
# Health check: below this share of listings with coordinates the geocoder
# is probably broken (a parser/transliteration change), not the data.
GEOCODE_MIN_COVERAGE_PCT = 85

# ----------------------------------------------------------------------------
# 6. DIGEST / SITE settings
# ----------------------------------------------------------------------------
# Deal persistence
PRICE_DROP_MIN_PCT = 2.0    # only badge as PRICE_DROP if price dropped >= 2%

# Motivated-seller detection — a real asking-price drop plus either age on
# the market or repeated cutting behaviour gets a MOTIVATED badge and a
# place in the "Biggest price cuts" section. Thresholds differ per vertical
# because car asking prices are ~20x smaller than flat prices.
MOTIVATED_MIN_DROP_EUR_FLAT = 4000   # real € cut needed to count at all
MOTIVATED_MIN_DROP_EUR_CAR = 400
MOTIVATED_STALE_DAYS_FLAT = 45       # days-on-market before "stale"
MOTIVATED_STALE_DAYS_CAR = 30
# "Stale & stubborn" card — ads sitting far past MOTIVATED_STALE_DAYS with
# NO recorded cut (the opposite of a motivated seller; watch-list for the
# cuts that usually come eventually).
STALE_MIN_DAYS_FLAT = 75
STALE_TOP_N = 8
MOTIVATED_MIN_RELISTINGS = 3         # previous_listings = serial relister
MOTIVATED_MIN_TRAIL_DROPS = 2        # own trail: drops counted separately
MOTIVATED_CUTS_TOP_N = 8             # rows in the cuts section
STILL_ACTIVE_MAX_DAYS = 7   # don't show "still active" for deals shown > N days ago

# Archive retention: digests older than this are deleted from data/digests/
# and docs/archive/ by website.build(). Every day adds ~120 KB flat + ~100 KB
# car HTML stored twice, so an unbounded archive grows the repo by ~150 MB a
# year. The current day's pages (index.html / cars.html) are never pruned.
ARCHIVE_KEEP_DAYS = 30
# archive.html coverage strip: how many recent days to show presence/
# absence of flat + car digests for (a red cell = the run failed that day)
ARCHIVE_GAP_DAYS = 14

# ----------------------------------------------------------------------------
# 7. CHAT INJECTION (global rule 4)
#    After a local run the script copies a status prompt to the clipboard so it
#    can be pasted into the Cascade/Devin chat. Set to False to disable.
# ----------------------------------------------------------------------------
CHAT_INJECT_ENABLED = True
# Auto-paste guard (Windows only): after copying, the message is pasted
# with Ctrl+V only when the foreground window title matches this pattern
# (the Cascade/Devin chat). Otherwise the clipboard copy is kept and a
# hint is printed. Never submits — set CHAT_INJECT_SUBMIT to also press
# Enter after pasting.
CHAT_INJECT_WINDOW_RE = r"devin|cascade|windsurf"
CHAT_INJECT_SUBMIT = False

# ----------------------------------------------------------------------------
# 8. STATE / BAILIFF AUCTIONS (izsoles.ta.gov.lv)
#     The State Land Service e-auction site lists forced-sale auctions run by
#     bailiffs (zvērināti tiesu izpildītāji) plus state and municipal property.
#     Starting prices are often well below market because the goal is debt
#     recovery. Riga apartment auctions get their own digest section — they
#     are NOT mixed into the deal ranking (different purchase process:
#     registration, deposit, bidding; prices aren't comparable to regular
#     listings). Sorted by distance to the school.
# ----------------------------------------------------------------------------
IZSOLES_ENABLED = True
IZSOLES_BASE = "https://izsoles.ta.gov.lv"
IZSOLES_TIMEOUT = 30       # seconds per request
IZSOLES_DELAY = 1.0        # seconds between detail-page fetches (be polite)
IZSOLES_MAX_PAGES = 5      # pagination cap (path-based: /2, /3, ...)
IZSOLES_MAX_DETAILS = 30   # safety cap on detail pages fetched per run
# Auctions ending within this many days get a red ENDS badge and sort to
# the top of the section — a great deal at 17:00 tomorrow is useless if
# you need to register and deposit first.
AUCTION_ENDING_SOON_DAYS = 3

# ----------------------------------------------------------------------------
# 9. FILE PATHS (data dir is committed so history persists across CI runs)
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
SEEN_DEALS_JSON = os.path.join(DATA_DIR, "seen_deals.json")
LAST_DIGEST_JSON = os.path.join(DATA_DIR, "last_digest.json")
CAR_SEEN_JSON = os.path.join(DATA_DIR, "car_seen.json")
CAR_MARKET_SNAPSHOT_JSON = os.path.join(DATA_DIR, "car_market_snapshot.json")
CAR_MARKET_STATS_JSON = os.path.join(DATA_DIR, "car_market_stats.json")
CAR_MARKET_HISTORY_JSON = os.path.join(DATA_DIR, "car_market_history.json")
CAR_MARKET_HISTORY_MAX_POINTS = 120  # days of per-model median kept
CAR_MODEL_SCAN_JSON = os.path.join(DATA_DIR, "car_model_scans.json")
FLAT_MARKET_STATS_JSON = os.path.join(DATA_DIR, "flat_market_stats.json")
FLAT_MARKET_HISTORY_JSON = os.path.join(DATA_DIR, "flat_market_history.json")
FLAT_MARKET_HISTORY_MAX_POINTS = 120  # days of per-district median kept
FLAT_ACTIVE_JSON = os.path.join(DATA_DIR, "flat_active.json")

# .main.lock — written while main.run() is active so a second local run
# (double-click, overlapping manual runs) doesn't interleave writes to
# data/ and docs/ and corrupt them (observed 2026-10-03). Older than
# STALE_HOURS it's assumed to come from a crashed run and is taken over.
MAIN_LOCK_FILE = os.path.join(DATA_DIR, ".main.lock")
MAIN_LOCK_STALE_HOURS = 4
GONE_MAX_ROWS = 25    # flats "Disappeared" section cap
CAR_GONE_MAX_ROWS = 20  # cars "Gone since yesterday" section cap
# Gone-spike guardrail: when more than GONE_SPIKE_PCT % of yesterday's live
# ads (and at least GONE_SPIKE_MIN) vanish overnight, a partially-failed
# scrape is far more likely than a sales wave — flag it on the banner
# instead of letting the gone list pass as real.
GONE_SPIKE_PCT = 25
GONE_SPIKE_MIN = 15
# "Relisted" detection: a new listing is matched against ads that went
# gone within this window (same district+street+rooms, area within
# GONE_RELIST_AREA_DIFF_M2). Sellers who withdraw and repost — usually at
# a cut — are a motivated-seller signal.
GONE_RELIST_DAYS = 30
GONE_RELIST_AREA_DIFF_M2 = 3.0
HEALTH_STATE_JSON = os.path.join(DATA_DIR, "health_state.json")
# seen_deals.json entries not re-shown for this many days are pruned — the
# dict otherwise grows forever (2026-10-03: was a listed known issue).
SEEN_DEALS_TTL_DAYS = 60
# price_history.json entries whose newest activity (our_tracking date,
# cenumednieks fetched_at, or first_seen) is older than this are dropped —
# dead listings were accumulating forever.
PRICE_HISTORY_KEEP_DAYS = 120
# Car health: a source with at least this many raw ads but under ~5%
# eligible probably has a broken parser/field mapping.
CAR_MIN_EXPECTED_RAW = 50
PRICE_HISTORY_JSON = os.path.join(DATA_DIR, "price_history.json")
GEOCODE_CACHE_JSON = os.path.join(DATA_DIR, "geocode_cache.json")
DIGEST_DIR = os.path.join(DATA_DIR, "digests")
HISTORY_COLUMNS = [
    "scrape_date", "source", "deal_type", "id", "url", "district", "street",
    "rooms", "area_m2", "floor", "floor_num", "floor_total", "series",
    "price_eur", "price_per_m2", "title",
    "ad_slug", "old_price", "show_price_drop", "price_unit",
]
