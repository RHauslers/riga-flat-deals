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


if __name__ == "__main__":
    unittest.main()
