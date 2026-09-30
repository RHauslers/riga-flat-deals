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


if __name__ == "__main__":
    unittest.main()
