# -*- coding: utf-8 -*-
"""Flat-pipeline tests: classify badge logic and scoring fallbacks.
Pure unit tests — no filesystem or network."""
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import classify
import scoring
import flat_market
import car_market
import geocode
import health
import notifier
import utils
from scrapers import izsoles


def _flat(source="ss.com", lid="f1", price=60000, district="Zolitude",
          rooms=2, area=50.0, deal_type="sale", price_unit=None,
          lat=None, lon=None):
    return {"source": source, "id": lid, "price_eur": price,
            "district": district, "rooms": rooms, "area_m2": area,
            "price_per_m2": price / area if area else None,
            "deal_type": deal_type, "price_unit": price_unit,
            "lat": lat, "lon": lon,
            "url": f"https://{source}/x/{lid}"}


def _hist(price_per_m2, district="Zolitude", rooms=2):
    return {"deal_type": "sale", "district": district, "rooms": rooms,
            "price_per_m2": price_per_m2}


class TestClassify(unittest.TestCase):
    """Badge priority: NEW > PRICE_DROP > STILL_ACTIVE > REAPPEARED."""

    def test_new_listing_gets_new(self):
        main, still = classify.classify(
            {"sale": [(_flat(), 1.0, "zscore")]}, {}, {})
        self.assertEqual(len(main["sale"]), 1)
        self.assertEqual(main["sale"][0][3], "NEW")
        self.assertEqual(still["sale"], [])

    def test_seen_with_price_drop_gets_price_drop(self):
        l = _flat(price=58000)
        seen = {"ss.com:f1": {"last_shown_price": 60000,
                              "last_shown_date": date.today().isoformat()}}
        main, still = classify.classify({"sale": [(l, 1.0, "zscore")]},
                                        seen, {})
        badge, detail = main["sale"][0][3], main["sale"][0][4]
        self.assertEqual(badge, "PRICE_DROP")
        self.assertIn("60000", detail)
        self.assertEqual(still["sale"], [])

    def test_small_drop_does_not_count(self):
        # 0.5% drop is below PRICE_DROP_MIN_PCT -> falls through to
        # REAPPEARED (not in yesterday's digest)
        l = _flat(price=59700)
        seen = {"ss.com:f1": {"last_shown_price": 60000,
                              "last_shown_date": date.today().isoformat()}}
        main, _ = classify.classify({"sale": [(l, 1.0, "zscore")]}, seen, {})
        self.assertEqual(main["sale"][0][3], "REAPPEARED")

    def test_seen_in_yesterday_goes_to_still_active(self):
        l = _flat()
        seen = {"ss.com:f1": {"last_shown_price": 60000,
                              "last_shown_date": date.today().isoformat()}}
        last = {"sale": [{"key": "ss.com:f1"}]}
        main, still = classify.classify({"sale": [(l, 1.0, "zscore")]},
                                        seen, last)
        self.assertEqual(main["sale"], [])
        self.assertEqual(len(still["sale"]), 1)

    def test_stale_still_active_reappears(self):
        # in yesterday's top-N but last shown > STILL_ACTIVE_MAX_DAYS ago
        old = (date.today()
               - timedelta(days=config.STILL_ACTIVE_MAX_DAYS + 1)).isoformat()
        l = _flat()
        seen = {"ss.com:f1": {"last_shown_price": 60000,
                              "last_shown_date": old}}
        last = {"sale": [{"key": "ss.com:f1"}]}
        main, still = classify.classify({"sale": [(l, 1.0, "zscore")]},
                                        seen, last)
        self.assertEqual(main["sale"][0][3], "REAPPEARED")
        self.assertEqual(still["sale"], [])

    def test_daily_rental_badged_short_term(self):
        l = _flat(price=400, price_unit="day")
        main, _ = classify.classify({"sale": [(l, 1.0, "zscore")]}, {}, {})
        self.assertEqual(main["sale"][0][3], "SHORT_TERM")


class TestComparisonHeader(unittest.TestCase):
    def test_first_run(self):
        out = classify.comparison_header({}, {})
        self.assertIn("First run", out)

    def test_beats_and_falls_short(self):
        today_items = [(_flat(lid="n1"), 2.0, "zscore")]
        better = classify.comparison_header(
            {"sale": today_items},
            {"date": "2026-09-29",
             "sale": [{"key": "ss.com:old", "score": 1.0}]})
        self.assertIn("beats", better)
        self.assertIn("1 new deal", better)
        worse = classify.comparison_header(
            {"sale": today_items},
            {"date": "2026-09-29",
             "sale": [{"key": "ss.com:old", "score": 9.9}]})
        self.assertIn("falls short", worse)


class TestScoring(unittest.TestCase):
    """Force the numpy-free z-score fallback for determinism."""

    def setUp(self):
        p = mock.patch.object(scoring, "_HAS_NUMPY", False)
        p.start()
        self.addCleanup(p.stop)

    def test_cheap_ppu_scores_positive(self):
        # history ~1000 EUR/m2; candidate at 600 EUR/m2 -> z > 0
        history = [_hist(v) for v in (950, 1000, 1000, 1050, 1100)]
        cheap = _flat(price=30000, area=50.0)   # 600/m2
        pricey = _flat(lid="f2", price=65000, area=50.0)  # 1300/m2
        out = scoring.score_and_rank([cheap, pricey], history)
        scores = {e[0]["id"]: e[1] for e in out["sale"]}
        self.assertGreater(scores["f1"], 0)
        self.assertLess(scores["f2"], 0)
        self.assertGreater(scores["f1"], scores["f2"])  # sorted best first

    def test_proximity_blends_into_sale_score(self):
        # same value score; the flat next to the school must outrank
        history = [_hist(v) for v in (950, 1000, 1000, 1050, 1100)]
        near = _flat(lid="near", lat=config.SCHOOL_LAT,
                     lon=config.SCHOOL_LON)
        far = _flat(lid="far", lat=56.70, lon=24.10)  # ~28 km away
        out = scoring.score_and_rank([near, far], history)
        ids = [e[0]["id"] for e in out["sale"]]
        self.assertEqual(ids[0], "near")
        self.assertTrue(all("+prox" in e[2] for e in out["sale"]))

    def test_top_n_cap(self):
        history = [_hist(v) for v in (950, 1000, 1000, 1050, 1100)]
        many = [_flat(lid=f"f{i}", price=40000 + i) for i in range(40)]
        out = scoring.score_and_rank(many, history)
        self.assertEqual(len(out["sale"]), config.TOP_N_PER_TYPE)

    def test_no_history_scores_zero(self):
        out = scoring.score_and_rank([_flat()], [])
        self.assertEqual(out["sale"][0][1], 0.0)


class TestFlatMarket(unittest.TestCase):
    """District stats for the Market page (flat_market.py)."""

    def test_groups_by_district_sale_only(self):
        listings = [
            _flat(lid="a", price=60000, district="Zolitude"),
            _flat(lid="b", price=40000, district="Zolitude", area=50),
            _flat(lid="c", price=90000, district="Centre", rooms=1),
            _flat(lid="r", price=500, district="Centre", deal_type="rent"),
        ]
        stats = flat_market.compute_district_stats(
            listings, {}, date.today().isoformat())
        by = {s["district"]: s for s in stats}
        self.assertEqual(set(by), {"Zolitude", "Centre"})
        self.assertEqual(by["Zolitude"]["ads"], 2)
        self.assertEqual(by["Zolitude"]["median_price"], 50000)
        self.assertEqual(by["Zolitude"]["min_price"], 40000)
        self.assertIn("/x/b", by["Zolitude"]["min_url"])   # cheapest ad's URL
        self.assertEqual(by["Centre"]["ads"], 1)           # rent excluded

    def test_new_today_counts_first_seen(self):
        today = date.today().isoformat()
        listings = [_flat(lid="a"), _flat(lid="b")]
        price_data = {"ss.com:a": {"first_seen": today},
                      "ss.com:b": {"first_seen": "2026-09-01"}}
        stats = flat_market.compute_district_stats(listings, price_data, today)
        self.assertEqual(stats[0]["new_today"], 1)

    def test_sorted_by_ads_desc(self):
        listings = ([_flat(lid=f"z{i}", district="Zolitude")
                     for i in range(3)]
                    + [_flat(lid="c1", district="Centre")])
        stats = flat_market.compute_district_stats(listings, {}, "d")
        self.assertEqual(stats[0]["district"], "Zolitude")

    def test_section_html_renders_and_links(self):
        stats = [{"district": "Zolitude", "ads": 5, "new_today": 2,
                  "median_ppu": 1100.0, "median_price": 55000,
                  "min_price": 41000, "min_url": "https://x/y"}]
        html = flat_market.flat_section_html(stats, "2026-09-30")
        self.assertIn("flat-market", html)
        self.assertIn("Zolitude", html)
        self.assertIn("index.html?district=Zolitude", html)
        self.assertIn("https://x/y", html)
        self.assertEqual(flat_market.flat_section_html([], "d"), "")

    def test_save_load_round_trip(self):
        import tempfile
        stats = [{"district": "Zolitude", "ads": 2, "new_today": 1,
                  "median_ppu": 1000.0, "median_price": 50000,
                  "min_price": 40000, "min_url": "u"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "flat_stats.json")
            flat_market.save_stats(stats, "2026-09-30", 2, path=path)
            loaded = flat_market.load_stats(path)
        self.assertEqual(loaded["date"], "2026-09-30")
        self.assertEqual(loaded["districts"][0]["district"], "Zolitude")
        self.assertIsNone(flat_market.load_stats(
            os.path.join(tmp, "missing.json")))

    def test_build_page_flat_only(self):
        """Market page must render when only flat stats exist."""
        flat_data = {"date": "2026-09-30", "total": 2,
                     "districts": [{"district": "Zolitude", "ads": 2,
                                    "new_today": 0, "median_ppu": 1000.0,
                                    "median_price": 50000,
                                    "min_price": 40000, "min_url": "u"}]}
        with mock.patch.object(car_market, "load_stats", return_value=None), \
             mock.patch.object(car_market, "load_history", return_value={}), \
             mock.patch.object(flat_market, "load_stats",
                               return_value=flat_data):
            html = car_market.build_page()
        self.assertIn("flat-market", html)
        self.assertIn("Zolitude", html)

    def test_market_page_subtabs(self):
        """The Market page has Cars/Flats sub-tabs wired to panes."""
        flat_data = {"date": "2026-09-30", "total": 2,
                     "districts": [{"district": "Zolitude", "ads": 2,
                                    "new_today": 0, "median_ppu": 1000.0,
                                    "median_price": 50000,
                                    "min_price": 40000, "min_url": "u"}]}
        with mock.patch.object(car_market, "load_stats", return_value=None), \
             mock.patch.object(car_market, "load_history", return_value={}), \
             mock.patch.object(flat_market, "load_stats",
                               return_value=flat_data):
            html = car_market.build_page()
        self.assertIn('id="mtab-cars"', html)
        self.assertIn('id="mtab-flats"', html)
        self.assertIn('id="mpane-cars"', html)
        self.assertIn('id="mpane-flats"', html)
        self.assertIn("__mktTab", html)
        # ?m=flats / #flats activation hooks
        self.assertIn("URLSearchParams", html)
        self.assertIn("'mpane-' + p", html)


class TestGeocodeNormalisation(unittest.TestCase):
    """SS.com's English street text -> Nominatim-friendly candidates
    (2026-09-30: 35 cached misses, all of this shape)."""

    def _first(self, addr):
        return geocode.address_candidates(addr)[0][0]

    def test_transliteration_and_missing_type(self):
        self.assertEqual(self._first("anninmuizhas 20"), "anninmuizas iela 20")
        self.assertEqual(self._first("augshzemes 5"), "augszemes iela 5")
        self.assertEqual(self._first("shampetera 23"), "sampetera iela 23")

    def test_abbreviations(self):
        self.assertEqual(self._first("kurzemes pr. 104a"),
                         "kurzemes prospekts 104a")
        self.assertEqual(self._first("imantas 16. l. 18"),
                         "imantas 16. līnija 18")
        self.assertEqual(self._first("m. krūmu 18"), "mazā krūmu iela 18")

    def test_house_number_variants_fall_back_in_order(self):
        cands = [q for q, _ in geocode.address_candidates("jurmalas g. 82/2")]
        self.assertEqual(cands, ["jurmalas gatve 82 k-2", "jurmalas gatve 82",
                                 "jurmalas gatve"])
        precisions = [p for _, p in geocode.address_candidates("apuzes 51a")]
        self.assertEqual(precisions, ["house", "house", "street"])

    def test_truncated_and_orphan_letter(self):
        # truncated type word resolved by prefix — bulvāris and iela are
        # different streets ~1 km apart
        self.assertEqual(geocode.address_candidates("anninmuizhas boule.."),
                         [("anninmuizas bulvāris", "street")])
        self.assertEqual(geocode.address_candidates("anninmuizhas stree.."),
                         [("anninmuizas iela", "street")])
        self.assertEqual(geocode.address_candidates("kurzemes pros.."),
                         [("kurzemes prospekts", "street")])
        # unknown fragment -> dropped, default type
        self.assertEqual(geocode.address_candidates("dammes xyzq.."),
                         [("dammes iela", "street")])
        self.assertEqual(self._first("imantas 3. l. c"), "imantas 3. līnija")
        self.assertEqual(geocode.address_candidates(""), [])

    def test_already_clean_address_unchanged(self):
        self.assertEqual(self._first("Dammes iela 12"), "dammes iela 12")

    def test_geocode_tries_candidates_and_reports_precision(self):
        calls = []

        def fake(q):
            calls.append(q)
            # house-level lookups miss; the bare street resolves near school
            if q == "anninmuizas iela":
                return config.SCHOOL_LAT + 0.002, config.SCHOOL_LON
            return None, None

        with mock.patch.object(geocode, "_nominatim", side_effect=fake), \
             mock.patch.object(geocode.time, "sleep"):
            lat, lon, prec, n = geocode._geocode_address("anninmuizhas 20")
        self.assertEqual(calls, ["anninmuizas iela 20", "anninmuizas iela"])
        self.assertEqual(prec, "street")
        self.assertEqual(n, 2)
        self.assertIsNotNone(lat)

    def test_far_away_hit_is_rejected(self):
        with mock.patch.object(geocode, "_nominatim",
                               return_value=(57.5, 27.0)), \
             mock.patch.object(geocode.time, "sleep"):
            lat, lon, prec, _ = geocode._geocode_address("Dammes iela 12")
        self.assertIsNone(lat)
        self.assertIsNone(prec)


class TestGeocodeCache(unittest.TestCase):
    """Failed lookups must NOT be retried every run (the old code checked a
    'tried_today' key that was never written)."""

    def setUp(self):
        self.cache = {}
        mock.patch.object(geocode, "load_cache",
                          side_effect=lambda: dict(self.cache)).start()
        mock.patch.object(geocode, "save_cache",
                          side_effect=lambda d: self.cache.update(d)).start()
        self.addCleanup(mock.patch.stopall)

    def test_recent_failure_not_retried(self):
        self.cache["ss.com:zolitude:irlavas 5"] = {
            "lat": None, "lon": None, "fetched_at": date.today().isoformat()}
        l = _flat(district="Zolitude"); l["street"] = "irlavas 5"
        with mock.patch.object(geocode, "_geocode_address") as g:
            geocode.enrich_coordinates([l])
        g.assert_not_called()
        self.assertIsNone(l.get("lat"))

    def test_old_failure_is_retried_and_precision_recorded(self):
        old = (date.today()
               - timedelta(days=config.GEOCODE_RETRY_FAILED_DAYS + 1)).isoformat()
        self.cache["ss.com:zolitude:irlavas 5"] = {
            "lat": None, "lon": None, "fetched_at": old}
        l = _flat(district="Zolitude"); l["street"] = "irlavas 5"
        with mock.patch.object(geocode, "_geocode_address",
                               return_value=(56.94, 24.02, "street", 2)):
            geocode.enrich_coordinates([l])
        self.assertEqual(l["lat"], 56.94)
        self.assertEqual(l["geo_precision"], "street")
        self.assertEqual(self.cache["ss.com:zolitude:irlavas 5"]["precision"],
                         "street")

    def test_cached_hit_carries_precision(self):
        self.cache["ss.com:imanta:x 1"] = {"lat": 56.95, "lon": 24.01,
                                           "precision": "house",
                                           "fetched_at": "2026-09-01"}
        l = _flat(district="Imanta"); l["street"] = "x 1"
        with mock.patch.object(geocode, "_geocode_address") as g:
            geocode.enrich_coordinates([l])
        g.assert_not_called()
        self.assertEqual(l["geo_precision"], "house")

    def test_coverage_and_health_issue(self):
        ls = [_flat(lid=str(i), lat=56.9, lon=24.0) for i in range(3)]
        for i in range(7):
            l = _flat(lid=f"n{i}"); l["street"] = "s"; ls.append(l)
        cov = geocode.coverage(ls)
        self.assertEqual(cov, (3, 10))
        keys = [k for k, _ in health.evaluate({"ss.com": 10}, 60, cov)]
        self.assertIn("geocode_coverage", keys)
        ok = health.evaluate({"ss.com": 10}, 60, (10, 10))
        self.assertFalse(any(k == "geocode_coverage" for k, _ in ok))


class TestDigestEscaping(unittest.TestCase):
    """Scraped text lands in a public page — it must be escaped and links
    allow-listed (previously street/title/url were interpolated raw)."""

    def test_safe_url(self):
        self.assertEqual(utils.safe_url("https://www.ss.com/msg/a.html"),
                         "https://www.ss.com/msg/a.html")
        self.assertEqual(utils.safe_url("javascript:alert(1)"), "")
        self.assertEqual(utils.safe_url("http://x"), "")
        self.assertEqual(utils.safe_url(None), "")

    def test_rows_escape_and_allowlist(self):
        evil = "<img src=x onerror=alert(1)>"
        l = _flat(district=evil)
        l.update({"street": evil, "floor": evil, "url": "javascript:x",
                  "_school_km": 0.3, "geo_precision": "street", "lat": 1,
                  "lon": 1})
        main = notifier._main_row_html((l, 1.0, "z", "NEW", None))
        still = notifier._still_row_html((l, 1.0, "z"))
        near = notifier.build_near_school_html([l])
        for html in (main, still, near):
            self.assertNotIn(evil, html)
            self.assertIn("&lt;img", html)
            self.assertNotIn("javascript:", html)
        self.assertIn("~0.3 km", main)        # approximate-distance marker
        self.assertIn("~0.30 km", near)
        self.assertIn("street-level position", near)

    def test_auction_row_share_badge_and_escape(self):
        a = _flat(source="izsoles.ta.gov.lv", lid="u1")
        a.update({"title": "<b>x</b>", "series": "Auction",
                  "ownership_share": "1/2", "auction_start_price": 20000,
                  "auction_end": "2026-10-10", "url": "https://izsoles.ta.gov.lv/izsole/u1"})
        html = notifier.build_auctions_html([a])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertIn("SHARE 1/2", html)
        self.assertIn("https://izsoles.ta.gov.lv/izsole/u1", html)
        whole = notifier.build_auctions_html([{**a, "ownership_share": None}])
        self.assertNotIn("SHARE 1/2", whole)

    def test_map_popup_escaped_and_json_safe(self):
        l = _flat(lat=56.9, lon=24.0)
        l.update({"street": "</script><svg onload=alert(1)>",
                  "url": "javascript:x", "geo_precision": "street"})
        markers = geocode.get_map_data([l])
        self.assertNotIn("<svg", markers[0]["popup"])
        self.assertNotIn("javascript:", markers[0]["popup"])
        self.assertIn("~", markers[0]["popup"])
        page = notifier._build_map_html(markers)
        self.assertNotIn("</script><svg", page)
        self.assertNotIn("<\\/script><svg", page.split("var markers")[0])


class TestGoneTracking(unittest.TestCase):
    """gone.py — 'Disappeared — likely sold/removed' flat section."""

    def _prev(self):
        return [
            {"k": "ss.com:1", "p": 50000, "d": "Imanta", "s": "A iela 1",
             "u": "https://www.ss.com/a"},
            {"k": "ss.com:2", "p": 60000, "d": "Imanta", "s": "B iela 2",
             "u": "https://www.ss.com/b"},
            {"k": "city24.lv:9", "p": 70000, "d": "Zolitude", "s": "C iela",
             "u": "https://city24.lv/c"},
        ]

    def test_gone_only_for_sources_with_data(self):
        import gone
        # ss.com:1 vanished, ss.com:2 still live; city24 produced nothing
        # today -> its ad must NOT be reported as gone.
        out = gone.gone_rows(self._prev(), {"ss.com:2"}, {"ss.com"}, 25)
        self.assertEqual([r["k"] for r in out], ["ss.com:1"])

    def test_gone_empty_when_no_prev_or_no_sources(self):
        import gone
        self.assertEqual(gone.gone_rows([], {"x"}, {"ss.com"}, 25), [])
        self.assertEqual(gone.gone_rows(self._prev(), set(), set(), 25), [])
        # a silent source's ads are never 'gone'
        self.assertEqual(gone.gone_rows(self._prev(), set(),
                                        {"ss.com"}, 25),
                         [r for r in self._prev()
                          if r["k"].startswith("ss.com")])

    def test_gone_cap(self):
        import gone
        prev = [{"k": f"ss.com:{i}", "p": 1, "d": "", "s": "", "u": ""}
                for i in range(30)]
        self.assertEqual(len(gone.gone_rows(prev, set(), {"ss.com"}, 25)),
                         25)

    def test_flat_active_rows_shape(self):
        import gone
        rows = gone.flat_active_rows([_flat(lid="f9", district="Imanta")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["k"], "ss.com:f9")
        self.assertEqual(rows[0]["d"], "Imanta")
        self.assertTrue(rows[0]["u"].startswith("https://"))
        self.assertEqual(gone.flat_active_rows([{"source": None}]), [])

    def test_gone_html_section(self):
        rows = [{"k": "ss.com:1", "p": 55000, "d": "Imanta",
                 "s": "Zalves iela 3", "u": "https://www.ss.com/x"}]
        price_data = {"ss.com:1": {"first_seen": "2026-09-20"}}
        html = notifier.build_gone_html(rows, price_data, "2026-10-03")
        self.assertIn("Disappeared", html)
        self.assertIn("Zalves iela 3", html)
        self.assertIn("13 d", html)
        self.assertIn("https://www.ss.com/x", html)
        self.assertEqual(notifier.build_gone_html([]), "")

    def test_gone_html_escapes(self):
        evil = "<script>x</script>"
        rows = [{"k": "ss.com:1", "p": 1, "d": evil, "s": evil,
                 "u": "javascript:x"}]
        html = notifier.build_gone_html(rows, {})
        self.assertNotIn("<script>x", html)
        self.assertNotIn("javascript:", html)


class TestAuctionUrgency(unittest.TestCase):
    """Auctions ending soon sort first and get a red ENDS badge."""

    def _auction(self, lid, end, km=None):
        a = _flat(source="izsoles.ta.gov.lv", lid=lid)
        a.update({"title": f"Flat {lid}", "series": "Auction",
                  "auction_start_price": 20000, "auction_end": end,
                  "_school_km": km})
        return a

    def test_ending_soon_badge_and_sort(self):
        soon = (date.today() + timedelta(days=1)).isoformat()
        far = (date.today() + timedelta(days=30)).isoformat()
        a_soon = self._auction("u1", soon, km=5.0)   # far but urgent
        a_far = self._auction("u2", far, km=0.5)     # near but not urgent
        html = notifier.build_auctions_html([a_far, a_soon])
        self.assertIn("ENDS IN 1d", html)
        self.assertIn("ending &le;3d", html)
        # urgent row sorts before the nearer non-urgent one
        self.assertLess(html.index("Flat u1"), html.index("Flat u2"))

    def test_ends_today_label(self):
        a = self._auction("u1", date.today().isoformat())
        html = notifier.build_auctions_html([a])
        self.assertIn("ENDS TODAY", html)

    def test_not_urgent_no_badge(self):
        far = (date.today() + timedelta(days=30)).isoformat()
        html = notifier.build_auctions_html([self._auction("u1", far)])
        self.assertNotIn("ENDS IN", html)


class TestFlatMarketTrends(unittest.TestCase):
    """flat_market history append + Δ7d/Trend columns."""

    def test_append_history_same_day_updates(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hist.json")
            stats = [{"district": "Imanta", "median_ppu": 900,
                      "median_price": 45000, "ads": 10}]
            flat_market._append_history(stats, "2026-10-02", path)
            stats2 = [{"district": "Imanta", "median_ppu": 950,
                       "median_price": 46000, "ads": 11}]
            flat_market._append_history(stats2, "2026-10-02", path)
            flat_market._append_history(stats2, "2026-10-03", path)
            hist = flat_market.load_history(path)
            pts = hist["Imanta"]
            self.assertEqual(len(pts), 2)          # same-day updated
            self.assertEqual(pts[0][1], 950)
            self.assertEqual(pts[1][0], "2026-10-03")

    def test_delta_7d(self):
        # base = newest point >= 7 days before the latest (09-20, 900)
        pts = [["2026-09-20", 900, 50000, 5],
               ["2026-09-27", 880, 45000, 6],
               ["2026-10-03", 810, 40000, 7]]
        self.assertAlmostEqual(flat_market._delta_7d(pts), -10.0, places=1)
        self.assertIsNone(flat_market._delta_7d(pts[:1]))
        self.assertIsNone(flat_market._delta_7d([]))

    def test_section_renders_trend_columns(self):
        stats = [{"district": "Imanta", "ads": 10, "median_ppu": 900,
                  "median_price": 45000, "min_price": 30000,
                  "min_url": "https://x", "new_today": 2}]
        hist = {"Imanta": [["2026-09-20", 1000, 50000, 5],
                           ["2026-10-03", 900, 45000, 10]]}
        html = flat_market.flat_section_html(stats, "2026-10-03", hist)
        self.assertIn("Δ 7d", html)
        self.assertIn("<svg", html)
        self.assertIn("-10.0%", html)


class TestCarHealth(unittest.TestCase):
    def test_low_eligible_flagged(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keys = health.check_cars(
                {"ss.com": {"raw": 100, "eligible": 2}}, {})
        self.assertEqual(keys, ["low_eligible:ss.com"])
        self.assertIn("ISSUE (cars) low_eligible:ss.com", buf.getvalue())

    def test_source_failed_flagged(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keys = health.check_cars({}, {"pp.lv": "RuntimeError: boom"})
        self.assertEqual(keys, ["source_failed:pp.lv"])
        self.assertIn("boom", buf.getvalue())

    def test_healthy_no_issues(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keys = health.check_cars(
                {"ss.com": {"raw": 100, "eligible": 60},
                 "_drop_reasons": {"x": 1}}, {})
        self.assertEqual(keys, [])
        self.assertEqual(buf.getvalue(), "")


class TestFlatTimelineSparkline(unittest.TestCase):
    def test_timeline_has_sparkline(self):
        import price_history
        key = "ss.com:f1"
        hist = {key: {"cenumednieks": None, "first_seen": "2026-09-01",
                      "our_tracking": [{"date": "2026-09-01", "price": 60000},
                                       {"date": "2026-09-20", "price": 55000}]}}
        html = price_history.format_price_timeline_html(
            {"source": "ss.com", "id": "f1", "price_eur": 55000}, hist)
        self.assertIn("<svg", html)
        self.assertIn("55,000", html)


class TestAuctionShare(unittest.TestCase):
    def test_parse_ownership_share(self):
        s, rest = izsoles.parse_ownership_share(
            "1/2 domājamā daļa no Višķu iela 11 - 5")
        self.assertEqual((s, rest), ("1/2", "Višķu iela 11 - 5"))
        s, rest = izsoles.parse_ownership_share(
            "186/1000 dom. daļas no Lāčplēša iela 62 - 3")
        self.assertEqual((s, rest), ("186/1000", "Lāčplēša iela 62 - 3"))
        self.assertEqual(izsoles.parse_ownership_share("Zalves iela 44A - 6"),
                         (None, "Zalves iela 44A - 6"))


class TestDigestCoverageBanner(unittest.TestCase):
    """Flat digest outage banner + coverage line + title/lang (was: broken
    sources only showed in CI logs, and the page had no <title> at all)."""

    def test_coverage_line_and_health_box(self):
        html = notifier.build_html(
            {}, {}, "", "note",
            source_counts={"ss.com": 0, "city24.lv": 31},
            health_pairs=[("source_zero:ss.com", "returned 0 listings")],
            n_auctions=4)
        self.assertIn("scan:", html)
        self.assertIn("city24.lv 31", html)
        self.assertIn("4 auctions", html)
        self.assertIn("source_zero:ss.com", html)
        self.assertIn("coverage may be incomplete", html)

    def test_no_issues_no_box(self):
        html = notifier.build_html(
            {}, {}, "", "note", source_counts={"ss.com": 10})
        self.assertNotIn("coverage may be incomplete", html)

    def test_title_and_lang(self):
        html = notifier.build_html({}, {}, "", "note")
        self.assertIn('<html lang="en">', html)
        self.assertIn("<title>Riga flat deals", html)

    def test_auction_failed_empty_state(self):
        html = notifier.build_auctions_html([], failed=True)
        self.assertIn("scan failed", html)
        self.assertIn("No in-budget", notifier.build_auctions_html([]))


class TestAuctionRegAndDeposit(unittest.TestCase):
    """Register-by deadline + deposit surfaced in the auctions table; the
    earliest actionable deadline drives urgency."""

    def _auction(self, lid, end=None, reg=None, dep=None):
        a = _flat(source="izsoles.ta.gov.lv", lid=lid)
        a.update({"title": f"Flat {lid}", "auction_start_price": 20000,
                  "auction_end": end, "auction_register_until": reg,
                  "auction_deposit": dep})
        return a

    def test_reg_and_deposit_rendered(self):
        a = self._auction(
            "u1", end=(date.today() + timedelta(days=20)).isoformat(),
            reg=(date.today() + timedelta(days=9)).isoformat(), dep=2000)
        html = notifier.build_auctions_html([a])
        self.assertIn("dep.", html)
        self.assertIn("reg. by", html)
        self.assertNotIn("REG IN", html)   # 9d is past the 3d window

    def test_reg_deadline_drives_urgency(self):
        urgent = self._auction(
            "u1", end=(date.today() + timedelta(days=20)).isoformat(),
            reg=(date.today() + timedelta(days=1)).isoformat())
        calm = self._auction(
            "u2", end=(date.today() + timedelta(days=20)).isoformat(),
            reg=(date.today() + timedelta(days=15)).isoformat())
        html = notifier.build_auctions_html([calm, urgent])
        self.assertIn("REG IN 1d", html)
        self.assertLess(html.index("Flat u1"), html.index("Flat u2"))


class TestSeenDealsPrune(unittest.TestCase):
    """seen_deals entries older than SEEN_DEALS_TTL_DAYS are dropped and the
    file is written compactly."""

    def test_old_entries_pruned_and_compact(self):
        import history
        import json as _json
        import tempfile
        old = (date.today()
               - timedelta(days=config.SEEN_DEALS_TTL_DAYS + 10)).isoformat()
        seen = {"ss.com:old": {"last_shown_date": old},
                "ss.com:keep": {"last_shown_date":
                                date.today().isoformat()}}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seen.json")
            with mock.patch.object(config, "SEEN_DEALS_JSON", path):
                history.update_seen_deals(
                    {"sale": [(_flat(lid="keep"), 1.0, "z")]}, seen)
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            out = _json.loads(raw)
        self.assertIn("ss.com:keep", out)
        self.assertNotIn("ss.com:old", out)
        self.assertNotIn("\n", raw.strip())   # compact write


class TestPriceHistoryPrune(unittest.TestCase):
    """price_history entries dead longer than PRICE_HISTORY_KEEP_DAYS are
    dropped; legacy entries missing our_tracking are tolerated."""

    def test_stale_entries_dropped(self):
        import price_history
        import json as _json
        import tempfile
        old = (date.today()
               - timedelta(days=config.PRICE_HISTORY_KEEP_DAYS
                           + 10)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ph.json")
            dead = {
                "ss.com:dead": {"cenumednieks": {"fetched_at": old},
                                "our_tracking": [{"date": old, "price": 1}],
                                "first_seen": old},
                "city24.lv:legacy": {},     # missing our_tracking
            }
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(dead, f)
            listing = {"source": "city24.lv", "id": "legacy",
                       "price_eur": 60000}
            with mock.patch.object(price_history, "PRICE_HISTORY_JSON", path):
                hist = price_history.update_price_history([listing])
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        self.assertIn("city24.lv:legacy", hist)
        self.assertNotIn("ss.com:dead", hist)
        self.assertNotIn("\n", raw.strip())  # compact write

    def test_last_activity_picks_newest(self):
        import price_history
        e = {"our_tracking": [{"date": "2026-01-01"}],
             "cenumednieks": {"fetched_at": "2026-03-01"},
             "first_seen": "2025-12-01"}
        self.assertEqual(price_history._entry_last_activity(e), "2026-03-01")
        self.assertIsNone(price_history._entry_last_activity({}))


class TestEndedAuctionGone(unittest.TestCase):
    """Ended auctions are tracked like flats in the gone section."""

    def test_auction_rows_in_active_snapshot(self):
        import gone
        a = {"source": "izsoles.ta.gov.lv", "id": "u1",
             "price_eur": 33500, "district": "Riga",
             "street": "A iela 1", "url": "https://izsoles.ta.gov.lv/x"}
        rows = gone.flat_active_rows([a])
        self.assertEqual(rows[0]["k"], "izsoles.ta.gov.lv:u1")
        self.assertEqual(rows[0]["p"], 33500)

    def test_ended_auction_reported_and_tagged(self):
        import gone
        prev = [{"k": "izsoles.ta.gov.lv:u1", "p": 33500, "d": "Riga",
                 "s": "A iela 1", "u": "https://izsoles.ta.gov.lv/x"}]
        out = gone.gone_rows(prev, set(), {"izsoles.ta.gov.lv"}, 25)
        self.assertEqual(len(out), 1)
        html = notifier.build_gone_html(out, {})
        self.assertIn("auction", html)
        # a failed auction scan must NOT report its old rows as gone
        self.assertEqual(gone.gone_rows(prev, set(), {"ss.com"}, 25), [])


class TestAlsoOnBadge(unittest.TestCase):
    """Cross-source deduped flats show '(also on X)' next to the source."""

    def test_source_link_shows_also_on(self):
        l = _flat()
        l["also_on"] = ["city24.lv"]
        self.assertIn("also on city24.lv", notifier._source_link(l))
        # dict-shaped entries (car-style) work too
        l["also_on"] = [{"source": "city24.lv"}]
        self.assertIn("also on city24.lv", notifier._source_link(l))
        no_url = {**l, "url": "javascript:x"}
        self.assertIn("also on", notifier._source_link(no_url))

    def test_cheaper_duplicate_flagged(self):
        # same flat €1k cheaper on city24 -> flag + green delta link
        a = _flat(source="ss.com", lid="1", price=50000)
        a.update({"street": "Dammes iela 12", "url": "https://www.ss.com/x"})
        b = dict(a, source="city24.lv", id="9", price_eur=49000,
                 url="https://www.city24.lv/y")
        out, n = utils.dedupe_cross_source([a, b])
        self.assertEqual(n, 1)
        s = out[0]
        self.assertEqual(s["source"], "ss.com")
        self.assertEqual(s["also_cheaper"]["price"], 49000)
        html = notifier._source_link(s)
        self.assertIn("city24.lv/y", html)
        self.assertIn("1 000 EUR on city24.lv", html)
        # dearer duplicate -> no cheaper flag (fresh dicts: dedupe
        # mutates the survivor in place)
        a2 = _flat(source="ss.com", lid="1", price=50000)
        a2.update({"street": "Dammes iela 12",
                   "url": "https://www.ss.com/x"})
        c = dict(a2, source="city24.lv", id="9", price_eur=51000,
                 url="https://www.city24.lv/y")
        out2, _ = utils.dedupe_cross_source([a2, c])
        self.assertIsNone(out2[0].get("also_cheaper"))


class TestFlatMarketBackfill(unittest.TestCase):
    """helper_scripts/backfill_flat_market.py — seeds district trend series
    from history.csv (sale+in-budget+no-new-build, deduped per source:id)."""

    def test_rows_by_day_filters_and_dedupes(self):
        import csv as _csv
        import tempfile
        from helper_scripts.backfill_flat_market import _rows_by_day
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "h.csv")
            rows = [
                {"scrape_date": "2026-10-01", "source": "ss.com", "id": "1",
                 "deal_type": "sale", "district": "Imanta",
                 "price_eur": "50000", "price_per_m2": "1000",
                 "series": "Soviet", "title": "Flat"},
                # same-day price change -> last row wins, not double-counted
                {"scrape_date": "2026-10-01", "source": "ss.com", "id": "1",
                 "deal_type": "sale", "district": "Imanta",
                 "price_eur": "48000", "price_per_m2": "960",
                 "series": "Soviet", "title": "Flat"},
                # rent -> excluded
                {"scrape_date": "2026-10-01", "source": "ss.com", "id": "2",
                 "deal_type": "rent", "district": "Imanta",
                 "price_eur": "600", "price_per_m2": "12",
                 "series": "Soviet", "title": "Flat"},
                # new build -> excluded
                {"scrape_date": "2026-10-01", "source": "ss.com", "id": "3",
                 "deal_type": "sale", "district": "Imanta",
                 "price_eur": "80000", "price_per_m2": "1600",
                 "series": "New", "title": "Flat"},
                # below the price floor -> excluded
                {"scrape_date": "2026-10-01", "source": "ss.com", "id": "4",
                 "deal_type": "sale", "district": "Imanta",
                 "price_eur": "3000", "price_per_m2": "60",
                 "series": "Soviet", "title": "Flat"},
            ]
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = _csv.DictWriter(f, fieldnames=config.HISTORY_COLUMNS)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            per_day = _rows_by_day(path)
        imanta = per_day["2026-10-01"]["Imanta"]
        self.assertEqual(list(imanta), ["ss.com:1"])
        self.assertEqual(imanta["ss.com:1"]["price_eur"], "48000")


class TestRunLock(unittest.TestCase):
    """data/.main.lock prevents concurrent main.run() processes."""

    def _write_lock(self, path, pid=9999, age_h=0):
        import json as _j
        info = {"pid": pid,
                "ts": __import__("time").time() - age_h * 3600,
                "date": "2026-10-03"}
        with open(path, "w", encoding="utf-8") as f:
            _j.dump(info, f)

    def test_fresh_lock_aborts_run(self):
        import tempfile, main
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, ".main.lock")
            self._write_lock(lock)
            with mock.patch.object(config, "MAIN_LOCK_FILE", lock):
                out = main.run()
            self.assertIn("another run", out)

    def test_stale_lock_taken_over_and_released(self):
        import tempfile, time, main
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, ".main.lock")
            self._write_lock(lock, age_h=config.MAIN_LOCK_STALE_HOURS + 1)
            with mock.patch.object(config, "MAIN_LOCK_FILE", lock):
                self.assertTrue(main._acquire_run_lock())
                main._release_run_lock()
            self.assertFalse(os.path.exists(lock))

    def test_foreign_lock_not_released(self):
        import tempfile, main
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, ".main.lock")
            self._write_lock(lock, pid=-1)  # not our pid
            with mock.patch.object(config, "MAIN_LOCK_FILE", lock):
                main._release_run_lock()
            self.assertTrue(os.path.exists(lock))


class TestAuctionPrevBids(unittest.TestCase):
    """Auction NEW badges and bid-move deltas from yesterday's snapshot."""

    def _auctions(self):
        return [{"source": "izsoles.ta.gov.lv", "id": "a1",
                 "title": "Seen auction", "price_eur": 33000,
                 "auction_start_price": 28000,
                 "auction_current_bid": 33000,
                 "auction_end": "2026-10-10"},
                {"source": "izsoles.ta.gov.lv", "id": "a2",
                 "title": "New auction", "price_eur": 40000,
                 "auction_start_price": 40000,
                 "auction_end": "2026-10-12"}]

    def test_new_badge_and_bid_delta(self):
        html = notifier.build_auctions_html(
            self._auctions(),
            prev_bids={"izsoles.ta.gov.lv:a1": 31000})
        self.assertEqual(html.count("First seen in today's scan"), 1)
        self.assertIn("&#9650; was", html)  # bid went up

    def test_no_badges_without_tracking(self):
        for prev in (None, {}):
            html = notifier.build_auctions_html(
                self._auctions(), prev_bids=prev)
            self.assertNotIn("First seen in today's scan", html)
            self.assertNotIn("&#9650; was", html)


class TestMarketHistoryAppend(unittest.TestCase):
    """_append_history keeps series date-sorted and replaces same-date."""

    def test_sorted_insert_and_same_date_replace(self):
        import json, tempfile
        s = [{"district": "X", "median_ppu": 1000, "median_price": 50000,
              "ads": 5}]
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "h.json")
            flat_market._append_history(s, "2026-10-03", p)
            flat_market._append_history(s, "2026-10-01", p)
            flat_market._append_history(
                [{"district": "X", "median_ppu": 1100,
                  "median_price": 55000, "ads": 6}], "2026-10-03", p)
            with open(p, encoding="utf-8") as f:
                pts = json.load(f)["X"]
        self.assertEqual([pt[0] for pt in pts],
                         ["2026-10-01", "2026-10-03"])
        self.assertEqual(pts[-1][1], 1100)  # re-run replaced the point


class TestDelta7d(unittest.TestCase):
    """utils.delta_7d shared by flat and car market trend columns."""

    def test_change_vs_newest_point_7d_old(self):
        pts = [["2026-09-20", 3000], ["2026-10-03", 3300]]
        self.assertAlmostEqual(utils.delta_7d(pts), 10.0)
        self.assertIsNone(utils.delta_7d([["2026-10-03", 3000]]))
        # newest point too recent -> no 7d-old base -> None
        self.assertIsNone(utils.delta_7d(
            [["2026-10-01", 3000], ["2026-10-03", 3300]]))

    def test_car_market_delta_column(self):
        stats = [{"make": "BMW", "model": "320", "ads": 12,
                  "median_price": 4500, "min_price": 3900, "min_url": "u",
                  "median_year": 2008, "median_km": 250000,
                  "deals": 3, "new_today": 1}]
        hist = {"bmw|320": [["2026-09-20", 3000], ["2026-10-03", 3300]]}
        html = car_market.build_market_html(
            stats, "2026-10-03", 100, hist)
        self.assertIn("+10.0%", html)
        self.assertIn("Δ 7d", html)


class TestAtomicWriteJson(unittest.TestCase):
    """utils.write_json: tmp+replace so a failed write keeps the old file."""

    def test_failed_write_preserves_existing_file(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.json")
            utils.write_json(p, {"a": 1}, indent=None)
            self.assertEqual(json.load(open(p)), {"a": 1})
            with self.assertRaises(TypeError):
                utils.write_json(p, object())
            self.assertEqual(json.load(open(p)), {"a": 1})
            self.assertFalse(os.path.exists(p + ".tmp"))


class TestPriceHistoryIdless(unittest.TestCase):
    """Listings without source/id must not collapse into 'None' keys."""

    def test_idless_skipped(self):
        import tempfile, price_history
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ph.json")
            with mock.patch.object(price_history, "PRICE_HISTORY_JSON",
                                   path):
                out = price_history.update_price_history(
                    [{"source": "ss.com", "price_eur": 100},
                     {"id": "x", "price_eur": 100}])
            self.assertEqual(out, {})


class TestAuctionEndedLabel(unittest.TestCase):
    """Ended auctions sink to the bottom and read ENDED, not ENDS TODAY."""

    def _ended(self):
        return {"source": "izsoles.ta.gov.lv", "id": "e1",
                "title": "Over auc", "price_eur": 1,
                "auction_start_price": 1, "auction_end": "2026-10-01"}

    def test_ended_label_and_order(self):
        live = {"source": "izsoles.ta.gov.lv", "id": "e2",
                "title": "Live auc", "price_eur": 1,
                "auction_start_price": 1, "auction_end": "2099-01-01"}
        html = notifier.build_auctions_html([live, self._ended()])
        self.assertIn("ENDED</b>", html)
        self.assertLess(html.index("Live auc"), html.index("Over auc"))
        self.assertNotIn("ending", html.split("State & bailiff auctions")[1]
                         [:200])  # no "N ending ≤3d" for an ended row


class TestCenuMissCaching(unittest.TestCase):
    """A CenuMednieks miss is stamped and not re-fetched for a week."""

    def test_miss_stamped_not_refetched(self):
        import json, tempfile, price_history
        calls = []
        listing = {"url": "https://www.ss.com/msg/lv/x/abcde.html",
                   "source": "ss.com", "id": "x", "price_eur": 50000}
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "ph.json")
            with mock.patch.object(price_history, "fetch_cenumednieks",
                                   lambda sid: (calls.append(sid), None)[1]
                                   ), \
                 mock.patch.object(price_history.time, "sleep",
                                   lambda s: None), \
                 mock.patch.object(price_history,
                                   "PRICE_HISTORY_JSON", path):
                price_history.update_price_history([listing])
                price_history.update_price_history([listing])
                self.assertEqual(len(calls), 1)  # one fetch total
                with open(path, encoding="utf-8") as f:
                    entry = json.load(f)["ss.com:x"]
                self.assertTrue(entry.get("cenumednieks_attempt"))


if __name__ == "__main__":
    unittest.main()
