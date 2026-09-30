# -*- coding: utf-8 -*-
"""Car-search tests: scraper row/card parsing, cross-source dedupe,
comparable-listing scoring, digest/site building, and cars.run() failure
behaviour. All file writes happen in tempfile dirs — repo data/ and docs/
are never touched."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup

import config
import car_value
import car_digest
import car_market
import cars
import notifier
import website
import main
from scrapers import car_ss, car_pp


SS_ROW = (
    '<table><tr id="tr_123456">'
    '<td class="msg2"><a class="am" '
    'href="/msg/lv/transport/cars/volkswagen/passat/abc12.html">'
    'Volkswagen Passat 2.0 TDI</a></td>'
    '<td class="msga2-o">2011</td>'
    '<td class="msga2-o">2.0D</td>'
    '<td class="msga2-r">254 tūkst.</td>'
    '<td class="msga2-o">5,350 €</td></tr></table>'
)

PP_CARD = (
    '<a href="/lv/transports-un-tehnika/vieglie-auto/volkswagen/passat/'
    'passat/!12345">'
    '<img alt="Pārdod - Volkswagen Passat">'
    '<h3 data-test="classified-title">Volkswagen Passat 2.0 TDI</h3>'
    '<div title="Degvielas tips">Dīzelis</div>'
    '<div title="Motora tilpums">2.0</div>'
    '<div title="Virsbūves tips">Universāls</div>'
    '<div title="Ātrumkārba">Manuālā</div>'
    '<div title="Izlaiduma gads">2012</div>'
    '<div title="Nobraukums, km">230 000</div>'
    '<div class="grid__view__price"><div class="me-auto">3 900 €</div></div>'
    '</a>'
)

MAKES_HTML = (
    '<a href="/lv/transport/cars/volkswagen/sell/">Volkswagen</a>'
    '<a href="/lv/transport/cars/skoda/sell/">Skoda</a>'
    '<a href="/lv/transport/cars/new/">Jaunie</a>'
    '<a href="/lv/transport/cars/search/">Search</a>'
    '<a href="/lv/transport/cars/exchange/">Exchange</a>'
    '<a href="/lv/transport/cars/volkswagen/">VW index</a>'
)


def _car(source="ss.com", lid="1", price=3900, year=2012, mileage=230000,
         engine=2.0, fuel="diesel", gearbox="manual", body="wagon",
         make="volkswagen", model="passat-b7"):
    return {
        "source": source, "id": lid,
        "url": f"https://{source}/x/{lid}",
        "make": make, "model": model, "year": year,
        "mileage_km": mileage, "fuel": fuel, "engine_l": engine,
        "gearbox": gearbox, "body": body, "price_eur": price,
        "title": "t", "location": "",
    }


def _ss_row(lid, title="Volkswagen Passat 2.0 TDI", mileage="254 tūkst."):
    return SS_ROW.replace("tr_123456", f"tr_{lid}") \
        .replace("Volkswagen Passat 2.0 TDI", title) \
        .replace("254 tūkst.", mileage)


class TestSSDiscoveryAndPagination(unittest.TestCase):
    def test_discover_makes_excludes_non_make_links(self):
        with mock.patch.object(car_ss, "_fetch", return_value=MAKES_HTML):
            makes = car_ss._discover_makes()
        self.assertEqual(makes, ["volkswagen", "skoda"])

    def test_pagination_uses_pageN_html_not_query(self):
        base = "https://www.ss.com/lv/transport/cars/volkswagen/sell/"
        pages = {
            base: f"<table>{_ss_row(1)}{_ss_row(2)}{_ss_row(3)}</table>",
            base + "page2.html": f"<table>{_ss_row(4)}{_ss_row(5)}{_ss_row(6)}</table>",
        }
        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            return pages[url]

        results, seen = [], set()
        with mock.patch.object(car_ss, "_fetch", side_effect=fake_fetch):
            car_ss._scrape_pages(base, 2, results, seen)
        self.assertEqual(fetched, [base, base + "page2.html"])
        self.assertFalse(any("?page=" in u for u in fetched))
        self.assertEqual({l["id"] for l in results},
                         {"1", "2", "3", "4", "5", "6"})

    def test_scrape_deep_scans_every_observed_model(self):
        makes_index = ('<a href="/lv/transport/cars/volkswagen/sell/">VW</a>'
                       '<a href="/lv/transport/cars/skoda/sell/">Skoda</a>')
        base_vw = "https://www.ss.com/lv/transport/cars/volkswagen/sell/"
        base_sk = "https://www.ss.com/lv/transport/cars/skoda/sell/"
        skoda_row = _ss_row(3).replace("cars/volkswagen/", "cars/skoda/")
        pages = {
            config.CAR_SS_MAKES_URL: makes_index,
            base_vw: f"<table>{_ss_row(1)}{_ss_row(2)}</table>",
            base_sk: f"<table>{skoda_row}</table>",
            "https://www.ss.com/lv/transport/cars/volkswagen/passat/sell/":
                f"<table>{_ss_row(4)}</table>",
            "https://www.ss.com/lv/transport/cars/skoda/passat/sell/":
                f"<table>{_ss_row(5).replace('cars/volkswagen/', 'cars/skoda/')}</table>",
        }
        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            return pages[url]

        with mock.patch.object(car_ss, "_fetch", side_effect=fake_fetch):
            results = car_ss.scrape(max_pages_per_make=1,
                                    max_pages_per_model=1, max_models=10)
        self.assertIn("https://www.ss.com/lv/transport/cars/volkswagen/passat/sell/",
                      fetched)
        self.assertIn("https://www.ss.com/lv/transport/cars/skoda/passat/sell/",
                      fetched)
        self.assertEqual({l["id"] for l in results}, {"1", "2", "3", "4", "5"})
        self.assertFalse(any("page=" in u for u in fetched))

    def test_model_deep_scan_caps_by_observed_volume(self):
        makes_index = '<a href="/lv/transport/cars/volkswagen/sell/">VW</a>'
        base = "https://www.ss.com/lv/transport/cars/volkswagen/sell/"

        def row_for(lid, model):
            return _ss_row(lid).replace("cars/volkswagen/passat/",
                                        f"cars/volkswagen/{model}/")

        pages = {
            config.CAR_SS_MAKES_URL: makes_index,
            base: f"<table>{row_for(1, 'golf-5')}{row_for(2, 'golf-5')}"
                  f"{row_for(3, 'polo')}</table>",
            "https://www.ss.com/lv/transport/cars/volkswagen/golf-5/sell/":
                f"<table>{row_for(4, 'golf-5')}</table>",
        }
        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            return pages[url]

        with mock.patch.object(car_ss, "_fetch", side_effect=fake_fetch):
            car_ss.scrape(max_pages_per_make=1, max_pages_per_model=1,
                          max_models=1)
        self.assertIn("https://www.ss.com/lv/transport/cars/volkswagen/golf-5/sell/",
                      fetched)
        self.assertNotIn("https://www.ss.com/lv/transport/cars/volkswagen/polo/sell/",
                         fetched)


class TestSSParsing(unittest.TestCase):
    def test_row_parses_verified_structure(self):
        tr = BeautifulSoup(SS_ROW, "lxml").find("tr")
        l = car_ss._row_to_listing(tr)
        self.assertIsNotNone(l)
        self.assertEqual(l["mileage_km"], 254000)
        self.assertEqual(l["year"], 2011)
        self.assertEqual(l["fuel"], "diesel")
        self.assertEqual(l["engine_l"], 2.0)
        self.assertEqual(l["price_eur"], 5350)
        self.assertEqual(l["make"], "volkswagen")
        self.assertEqual(l["model"], "passat-b7")
        self.assertTrue(l["url"].startswith("https://www.ss.com/"))

    def test_buy_ad_and_missing_mileage_invalid(self):
        html = _ss_row(1, title="Pērku Passat")
        tr = BeautifulSoup(html, "lxml").find("tr")
        self.assertIsNone(car_ss._row_to_listing(tr))

        html = _ss_row(1, mileage="-")
        tr = BeautifulSoup(html, "lxml").find("tr")
        l = car_ss._row_to_listing(tr)
        self.assertIsNotNone(l)
        self.assertIsNone(l["mileage_km"])
        self.assertFalse(car_value.eligible(l))

    def test_title_with_replaced_parts_still_parses(self):
        html = _ss_row(1, title="Mainīta eļļa, pārdodu Passat")
        tr = BeautifulSoup(html, "lxml").find("tr")
        self.assertIsNotNone(car_ss._row_to_listing(tr))

    def test_tukst_fraction_mileage(self):
        for raw, want in (("254,5 tūkst.", 254500), ("254.5 tūkst.", 254500)):
            html = _ss_row(1, mileage=raw)
            tr = BeautifulSoup(html, "lxml").find("tr")
            l = car_ss._row_to_listing(tr)
            self.assertEqual(l["mileage_km"], want, raw)

    def test_title_lpg_overrides_petrol_engine(self):
        html = _ss_row(1, title="Passat B7 Benzīns + Gāze") \
            .replace("2.0D", "1.8")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertEqual(l["fuel"], "lpg")

        for title in ("Passat B7 Gāze/Benzīns", "Passat B7 LPG"):
            html = _ss_row(1, title=title).replace("2.0D", "1.8")
            l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
            self.assertEqual(l["fuel"], "lpg", title)

        html = _ss_row(1, title="Passat B7 benzīns, bez gāzes") \
            .replace("2.0D", "1.8")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertEqual(l["fuel"], "petrol")

        html = _ss_row(1, title="Passat B7 Benzīns + Gāze")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertEqual(l["fuel"], "diesel")

    def test_latvian_title_specs(self):
        html = _ss_row(1, title="Pārdodu Passat, automātiskā, universāls")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertEqual(l["gearbox"], "automatic")
        self.assertEqual(l["body"], "wagon")

        html = _ss_row(1, title="Pārdodu Passat, mehāniskā")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertEqual(l["gearbox"], "manual")
        self.assertIsNone(l["body"])

        html = _ss_row(1, title="Pārdodu Passat")
        l = car_ss._row_to_listing(BeautifulSoup(html, "lxml").find("tr"))
        self.assertIsNone(l["gearbox"])
        self.assertIsNone(l["body"])


class TestPPParsing(unittest.TestCase):
    def test_card_parses_thin_space_amounts_and_b7(self):
        a = BeautifulSoup(PP_CARD, "lxml").find("a")
        l = car_pp._card_to_listing(a)
        self.assertIsNotNone(l)
        self.assertEqual(l["id"], "12345")
        self.assertEqual(l["price_eur"], 3900)
        self.assertEqual(l["mileage_km"], 230000)
        self.assertEqual(l["year"], 2012)
        self.assertEqual(l["fuel"], "diesel")
        self.assertEqual(l["gearbox"], "manual")
        self.assertEqual(l["body"], "wagon")
        self.assertEqual(l["make"], "volkswagen")
        self.assertEqual(l["model"], "passat-b7")
        self.assertTrue(l["url"].startswith("https://pp.lv/"))

    def test_card_mehaniska_is_manual(self):
        html = PP_CARD.replace("Manuālā", "Mehāniskā")
        a = BeautifulSoup(html, "lxml").find("a")
        l = car_pp._card_to_listing(a)
        self.assertEqual(l["gearbox"], "manual")

    def test_card_fuel_map_literal_values(self):
        for raw, want in (("Benzīns/Hibrīds", "hybrid"),
                          ("Gāze/Benzīns", "lpg"),
                          ("Dīzelis", "diesel"),
                          ("Benzīns", "petrol")):
            html = PP_CARD.replace("Dīzelis", raw)
            a = BeautifulSoup(html, "lxml").find("a")
            l = car_pp._card_to_listing(a)
            self.assertEqual(l["fuel"], want, raw)

    def test_non_seller_cards_skipped(self):
        for alt in ("Pērk - x", "Maiņa - x", "Izīrē - x", ""):
            html = PP_CARD.replace('alt="Pārdod - Volkswagen Passat"',
                                   f'alt="{alt}"')
            a = BeautifulSoup(html, "lxml").find("a")
            self.assertIsNone(car_pp._card_to_listing(a), alt)


class TestCarDedupe(unittest.TestCase):
    def test_same_car_merges_and_keeps_other_url(self):
        a = _car("ss.com", "s1", 3900)
        a["url"] = "https://www.ss.com/msg/lv/transport/cars/volkswagen/passat/x.html"
        b = _car("pp.lv", "p1", 3900)
        b["url"] = "https://pp.lv/lv/x/!p1"
        out, merged = car_value.dedupe_cross_source([a, b])
        self.assertEqual(merged, 1)
        self.assertEqual(len(out), 1)
        urls = {l.get("url") for l in out[0].get("also_on", [])} | {out[0]["url"]}
        self.assertEqual(urls, {a["url"], b["url"]})

    def test_mismatched_specs_do_not_merge(self):
        base = _car("ss.com", "s1", 3900)
        cases = [
            _car("pp.lv", "p1", 3900, gearbox="automatic"),
            _car("pp.lv", "p2", 3900, body="sedan"),
            _car("pp.lv", "p3", 4000),
            _car("pp.lv", "p4", 3900, year=2011),
            _car("pp.lv", "p5", 3900, mileage=231000),
            _car("pp.lv", "p6", 3900, engine=1.9),
            _car("pp.lv", "p7", 3900, fuel="petrol"),
        ]
        for other in cases:
            out, merged = car_value.dedupe_cross_source([dict(base), other])
            self.assertEqual(merged, 0, other["id"])
            self.assertEqual(len(out), 2, other["id"])


class TestCarScoring(unittest.TestCase):
    def _peers(self, prices, prefix="p"):
        return [_car("pp.lv", f"{prefix}{i}", p) for i, p in enumerate(prices)]

    def test_3000_vs_peers_scores_82_and_qualifies(self):
        cand = _car("ss.com", "c1", 3000)
        listings = [cand] + self._peers([4200, 4300, 4500, 4700])
        qualified, assessed = car_value.score_and_rank(listings)
        scored = next(l for l in assessed
                      if l["source"] == "ss.com" and l["id"] == "c1")
        self.assertEqual(scored["_median"], 4400)
        self.assertEqual(scored["_comps"], 4)
        self.assertEqual(scored["_score"], 82)
        self.assertTrue(scored["_good"])
        self.assertIn(scored, qualified)

    def test_6500_excluded_as_candidate_but_peer_eligible(self):
        cand = _car("ss.com", "c1", 3000)
        over = _car("pp.lv", "big", 6500)
        listings = [cand, over] + self._peers([4200, 4300, 4500, 4700], "q")
        qualified, assessed = car_value.score_and_rank(listings)
        self.assertTrue(car_value.eligible(over))
        self.assertNotIn("big", {l["id"] for l in assessed})
        scored = next(l for l in assessed if l["id"] == "c1")
        self.assertEqual(scored["_comps"], 5)
        self.assertTrue(scored["_good"])

    def test_fewer_than_4_comparables_not_good(self):
        cand = _car("ss.com", "c1", 3000)
        listings = [cand] + self._peers([4200, 4300, 4500])
        qualified, assessed = car_value.score_and_rank(listings)
        scored = next(l for l in assessed if l["id"] == "c1")
        self.assertIsNone(scored["_score"])
        self.assertFalse(scored["_good"])
        self.assertEqual(qualified, [])

    def test_low_mileage_and_newer_year_lift_score(self):
        # identical 13.6% discount vs the same peers; only condition differs
        fresh = _car("ss.com", "fresh", 3800, mileage=170000, year=2014)
        worn = _car("ss.com", "worn", 3800, mileage=290000, year=2010)
        peers = self._peers([4200, 4300, 4500, 4700], "m")
        qualified, assessed = car_value.score_and_rank([fresh, worn] + peers)
        by_id = {l["id"]: l for l in assessed}
        self.assertEqual(by_id["fresh"]["_pool_year"], 2012)
        self.assertEqual(by_id["fresh"]["_pool_mileage"], 230000)
        self.assertEqual(by_id["fresh"]["_score"], 80)
        self.assertEqual(by_id["worn"]["_score"], 48)
        self.assertGreater(by_id["fresh"]["_score"], by_id["worn"]["_score"])


class TestBadges(unittest.TestCase):
    def test_still_active_only_when_shown_today_or_yesterday(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        old = (date.today() - timedelta(days=10)).isoformat()
        self.assertEqual(
            cars._badge({"last_shown": yesterday, "last_price": 3900}, 3900),
            "STILL ACTIVE")
        self.assertEqual(
            cars._badge({"last_shown": old, "last_price": 3900}, 3900),
            "REAPPEARED")
        self.assertEqual(
            cars._badge({"last_shown": today, "last_price": 3900}, 3900),
            "STILL ACTIVE")

    def test_same_day_rerun_keeps_new_badge(self):
        today = date.today().isoformat()
        self.assertEqual(
            cars._badge({"last_shown": today, "first_shown": today,
                         "last_price": 3900}, 3900),
            "NEW")
        self.assertEqual(
            cars._badge({"last_shown": today, "first_seen": today,
                         "last_price": 3900}, 3900),
            "NEW")

    def test_price_drop_wins_regardless_of_age(self):
        old = (date.today() - timedelta(days=30)).isoformat()
        self.assertEqual(
            cars._badge({"last_shown": old, "last_price": 4000}, 3900),
            "PRICE DROP")
        self.assertEqual(
            cars._badge({"last_shown": old, "last_price": 4000}, 3950),
            "REAPPEARED")


class _TempPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.digest_dir = os.path.join(self.tmp.name, "digests")
        self.seen_json = os.path.join(self.tmp.name, "car_seen.json")
        self.snapshot_json = os.path.join(self.tmp.name,
                                          "car_market_snapshot.json")
        self.stats_json = os.path.join(self.tmp.name,
                                       "car_market_stats.json")
        os.makedirs(self.digest_dir)
        self._patches = [
            mock.patch.object(config, "DIGEST_DIR", self.digest_dir),
            mock.patch.object(config, "CAR_SEEN_JSON", self.seen_json),
            mock.patch.object(config, "CAR_MARKET_SNAPSHOT_JSON",
                              self.snapshot_json),
            mock.patch.object(config, "CAR_MARKET_STATS_JSON",
                              self.stats_json),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class TestCarsRun(_TempPaths):
    def test_both_sources_fail_no_state_mutation(self):
        with open(self.seen_json, "w", encoding="utf-8") as f:
            json.dump({"ss.com:old": {"first_seen": "2026-09-01",
                                      "last_seen": "2026-09-01",
                                      "last_price": 3000,
                                      "last_shown": "2026-09-01"}}, f)
        before = open(self.seen_json, "rb").read()
        with open(self.snapshot_json, "w", encoding="utf-8") as f:
            f.write('{"date": "2000-01-01", "listings": []}')
        snap_before = open(self.snapshot_json, "rb").read()
        with mock.patch.object(cars.car_ss, "scrape",
                               side_effect=RuntimeError("ss boom")), \
             mock.patch.object(cars.car_pp, "scrape",
                               side_effect=RuntimeError("pp boom")):
            status = cars.run()
        self.assertIn("failed", status)
        self.assertEqual(open(self.seen_json, "rb").read(), before)
        self.assertEqual(open(self.snapshot_json, "rb").read(), snap_before)
        today = date.today().isoformat()
        out = os.path.join(self.digest_dir, f"cars_{today}.html")
        self.assertTrue(os.path.exists(out))
        html_text = open(out, encoding="utf-8").read()
        self.assertIn("no current data", html_text.lower())
        self.assertIn("ss.com", html_text)
        self.assertIn("pp.lv", html_text)

    def test_both_sources_empty_is_failure(self):
        with mock.patch.object(cars.car_ss, "scrape", return_value=[]), \
             mock.patch.object(cars.car_pp, "scrape", return_value=[]):
            status = cars.run()
        self.assertIn("failed", status)
        self.assertFalse(os.path.exists(self.seen_json))
        self.assertFalse(os.path.exists(self.snapshot_json))
        today = date.today().isoformat()
        html_text = open(os.path.join(self.digest_dir,
                                      f"cars_{today}.html"),
                         encoding="utf-8").read()
        self.assertIn("no current data", html_text.lower())
        self.assertIn("no eligible car listings", html_text)

    def test_one_source_fails_banner_and_state_written(self):
        listings = [_car("pp.lv", "c1", 3000)] + \
                   [_car("pp.lv", f"p{i}", p)
                    for i, p in enumerate([4200, 4300, 4500, 4700])]
        with mock.patch.object(cars.car_ss, "scrape",
                               side_effect=RuntimeError("ss boom")), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            status = cars.run()
        self.assertIn("ss boom", status)
        today = date.today().isoformat()
        html_text = open(os.path.join(self.digest_dir, f"cars_{today}.html"),
                         encoding="utf-8").read()
        self.assertIn("ource outage", html_text)
        self.assertIn("ss.com", html_text)
        self.assertIn("NEW", html_text)
        seen = json.load(open(self.seen_json, encoding="utf-8"))
        self.assertEqual(seen["pp.lv:c1"]["last_shown"], today)

        snap = json.load(open(self.snapshot_json, encoding="utf-8"))
        self.assertEqual(snap["date"], today)
        self.assertEqual(len(snap["listings"]), 5)
        for item in snap["listings"]:
            self.assertEqual(set(item.keys()),
                             set(config.CAR_SNAPSHOT_FIELDS))
            for field in ("year", "mileage_km", "price_eur"):
                self.assertIsInstance(item[field], (int, float))
        self.assertNotIn("url", snap["listings"][0])
        self.assertNotIn("title", snap["listings"][0])

    def test_same_day_second_run_keeps_new_badge(self):
        listings = [_car("pp.lv", "c1", 3000)] + \
                   [_car("pp.lv", f"p{i}", p)
                    for i, p in enumerate([4200, 4300, 4500, 4700])]
        with mock.patch.object(cars.car_ss, "scrape", return_value=[]), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            cars.run()
            cars.run()
        today = date.today().isoformat()
        html_text = open(os.path.join(self.digest_dir, f"cars_{today}.html"),
                         encoding="utf-8").read()
        self.assertIn("NEW", html_text)
        self.assertNotIn("STILL ACTIVE", html_text)
        seen = json.load(open(self.seen_json, encoding="utf-8"))
        self.assertEqual(seen["pp.lv:c1"]["first_shown"], today)
        self.assertEqual(seen["pp.lv:c1"]["last_shown"], today)

    def test_price_trail_accumulates_and_renders(self):
        today = date.today().isoformat()
        yday = (date.today() - timedelta(days=1)).isoformat()
        with open(self.seen_json, "w", encoding="utf-8") as f:
            json.dump({"pp.lv:c1": {"first_seen": yday, "last_seen": yday,
                                    "last_price": 5000, "last_shown": yday,
                                    "first_shown": yday,
                                    "prices": [[yday, 5000]]}}, f)
        listings = [_car("pp.lv", "c1", 4000)] + \
                   [_car("pp.lv", f"p{i}", p)
                    for i, p in enumerate([5200, 5300, 5500, 5700])]
        with mock.patch.object(cars.car_ss, "scrape", return_value=[]), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            cars.run()
        seen = json.load(open(self.seen_json, encoding="utf-8"))
        self.assertEqual(seen["pp.lv:c1"]["prices"],
                         [[yday, 5000], [today, 4000]])
        html_text = open(os.path.join(self.digest_dir,
                                      f"cars_{today}.html"),
                         encoding="utf-8").read()
        self.assertIn("PRICE DROP", html_text)
        self.assertIn("seen 1 d", html_text)
        self.assertIn("→", html_text)          # €5,000 → €4,000 trail
        self.assertIn("<svg", html_text)       # sparkline

    def test_same_day_rerun_updates_todays_price_point(self):
        today = date.today().isoformat()
        listings = [_car("pp.lv", "c1", 3000)]
        with mock.patch.object(cars.car_ss, "scrape", return_value=[]), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            cars.run()
        listings[0]["price_eur"] = 2800
        with mock.patch.object(cars.car_ss, "scrape", return_value=[]), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            cars.run()
        seen = json.load(open(self.seen_json, encoding="utf-8"))
        self.assertEqual(seen["pp.lv:c1"]["prices"], [[today, 2800]])

    def test_unparseable_source_counts_as_failure(self):
        listings = [_car("pp.lv", "c1", 3000)] + \
                   [_car("pp.lv", f"p{i}", p)
                    for i, p in enumerate([4200, 4300, 4500, 4700])]
        garbage = [{"source": "ss.com", "id": "g1", "title": "?"}]
        with mock.patch.object(cars.car_ss, "scrape", return_value=garbage), \
             mock.patch.object(cars.car_pp, "scrape", return_value=listings):
            status = cars.run()
        self.assertIn("no eligible car listings", status)
        today = date.today().isoformat()
        html_text = open(os.path.join(self.digest_dir, f"cars_{today}.html"),
                         encoding="utf-8").read()
        self.assertIn("ource outage", html_text)
        seen = json.load(open(self.seen_json, encoding="utf-8"))
        self.assertEqual(seen["pp.lv:c1"]["last_shown"], today)


class TestWebsiteBuild(_TempPaths):
    def setUp(self):
        super().setUp()
        self.docs_dir = os.path.join(self.tmp.name, "docs")
        self.arch_dir = os.path.join(self.docs_dir, "archive")
        for p in (mock.patch.object(website, "DOCS_DIR", self.docs_dir),
                  mock.patch.object(website, "ARCHIVE_DIR", self.arch_dir)):
            p.start()
            self.addCleanup(p.stop)

    def _write(self, name, body):
        with open(os.path.join(self.digest_dir, name), "w",
                  encoding="utf-8") as f:
            f.write(body)

    def test_relative_tabs_archive_and_flat_digest_untouched(self):
        today = date.today().isoformat()
        flat = ("<html><body><h1>digest</h1><p>3 new/changed, "
                "2 still active</p></body></html>")
        car = ("<html><body><h1>cars</h1></body></html>")
        self._write(f"digest_{today}.html", flat)
        self._write(f"cars_{today}.html", car)

        website.build()

        index = open(os.path.join(self.docs_dir, "index.html"),
                     encoding="utf-8").read()
        cars_html = open(os.path.join(self.docs_dir, "cars.html"),
                         encoding="utf-8").read()
        archive = open(os.path.join(self.docs_dir, "archive.html"),
                       encoding="utf-8").read()
        self.assertIn('href="index.html"', index)
        self.assertIn('href="cars.html"', index)
        self.assertIn('href="archive.html"', index)
        self.assertIn('aria-current="page"', index)
        self.assertIn('href="index.html"', cars_html)
        self.assertIn('href="cars.html"', cars_html)
        self.assertIn('href="archive.html"', cars_html)
        self.assertNotIn("stale-warning", cars_html)
        self.assertIn(f"archive/digest_{today}.html", archive)
        self.assertIn(f"archive/cars_{today}.html", archive)
        self.assertIn("(today)", archive)

        arch_flat = open(os.path.join(self.arch_dir,
                                      f"digest_{today}.html"),
                         encoding="utf-8").read()
        arch_car = open(os.path.join(self.arch_dir, f"cars_{today}.html"),
                        encoding="utf-8").read()
        self.assertIn('href="../index.html"', arch_flat)
        self.assertIn('href="../cars.html"', arch_flat)
        self.assertIn('href="../archive.html"', arch_car)
        self.assertNotIn("stale-warning", arch_car)

        self.assertEqual(open(os.path.join(self.digest_dir,
                                           f"digest_{today}.html"),
                              encoding="utf-8").read(), flat)

    def test_market_tab_built_with_nav(self):
        today = date.today().isoformat()
        self._write(f"digest_{today}.html",
                    "<html><body><h1>digest</h1></body></html>")
        self._write(f"cars_{today}.html",
                    "<html><body><h1>cars</h1></body></html>")
        stats_path = os.path.join(self.tmp.name, "stats.json")
        car_market.save_stats(
            [{"make": "VW", "model": "Golf", "ads": 4,
              "median_price": 4000, "min_price": 3000,
              "min_url": "https://www.ss.com/x", "median_year": 2012.0,
              "median_km": 200000.0, "deals": 1}],
            today, 4, path=stats_path)
        with mock.patch.object(config, "CAR_MARKET_STATS_JSON",
                               stats_path):
            website.build()
        page = open(os.path.join(self.docs_dir, "market.html"),
                    encoding="utf-8").read()
        self.assertIn('href="market.html"', page)
        self.assertIn('aria-current="page"', page)
        self.assertIn("Golf", page)
        # the other tabs must link to it too
        index = open(os.path.join(self.docs_dir, "index.html"),
                     encoding="utf-8").read()
        self.assertIn('href="market.html"', index)
        # missing stats -> placeholder, still navigable
        with mock.patch.object(config, "CAR_MARKET_STATS_JSON",
                               os.path.join(self.tmp.name, "none.json")):
            website.build()
        page = open(os.path.join(self.docs_dir, "market.html"),
                    encoding="utf-8").read()
        self.assertIn("Not generated yet", page)
        self.assertIn('href="index.html"', page)

    def test_yesterday_car_digest_shows_stale_banner(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._write(f"digest_{today}.html",
                    "<html><body><h1>digest</h1></body></html>")
        self._write(f"cars_{yesterday}.html",
                    "<html><body><h1>cars</h1></body></html>")

        website.build()

        cars_html = open(os.path.join(self.docs_dir, "cars.html"),
                         encoding="utf-8").read()
        self.assertIn("stale-warning", cars_html)
        self.assertIn(yesterday, cars_html)
        self.assertIn("no fresh car digest", cars_html)

        archive = open(os.path.join(self.docs_dir, "archive.html"),
                       encoding="utf-8").read()
        self.assertIn(f"archive/cars_{yesterday}.html", archive)
        self.assertIn(f"{yesterday} (latest)", archive)
        self.assertNotIn(f"{yesterday} (today)", archive)

        arch_car = open(os.path.join(self.arch_dir, f"cars_{yesterday}.html"),
                        encoding="utf-8").read()
        self.assertNotIn("stale-warning", arch_car)

    def test_yesterday_flat_digest_stale_banner(self):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._write(f"digest_{yesterday}.html",
                    "<html><body><h1>digest</h1></body></html>")
        self._write(f"cars_{today}.html",
                    "<html><body><h1>cars</h1></body></html>")

        website.build()

        index = open(os.path.join(self.docs_dir, "index.html"),
                     encoding="utf-8").read()
        self.assertIn("stale-warning", index)
        self.assertIn(yesterday, index)
        self.assertIn("no fresh flat digest", index)

        archive = open(os.path.join(self.docs_dir, "archive.html"),
                       encoding="utf-8").read()
        self.assertIn(f"{yesterday} (latest)", archive)
        self.assertNotIn(f"{yesterday} (today)", archive)

        arch_flat = open(os.path.join(self.arch_dir,
                                      f"digest_{yesterday}.html"),
                         encoding="utf-8").read()
        self.assertNotIn("stale-warning", arch_flat)

        cars_html = open(os.path.join(self.docs_dir, "cars.html"),
                         encoding="utf-8").read()
        self.assertNotIn("stale-warning", cars_html)
        self.assertIn('href="index.html"', cars_html)

    def test_nav_ignores_body_text_inside_script(self):
        """A digest whose <script> mentions the literal text '<body>' (the
        budget JS comment does) must still get the nav inside the real
        body, not inside the script element."""
        today = date.today().isoformat()
        flat = ("<html><head><script>// input lives in <body> x"
                "</script></head><body><h1>digest</h1></body></html>")
        car = ("<html><head><script>// same <body> trap"
               "</script></head><body><h1>cars</h1></body></html>")
        self._write(f"digest_{today}.html", flat)
        self._write(f"cars_{today}.html", car)
        website.build()
        for f in ("index.html", "cars.html",
                  f"archive{os.sep}digest_{today}.html",
                  f"archive{os.sep}cars_{today}.html"):
            path = os.path.join(self.docs_dir, f)
            content = open(path, encoding="utf-8").read()
            nav_at = content.find('class="site-nav"')
            real_body = content.find("<body>")
            self.assertGreaterEqual(nav_at, 0, f)
            self.assertGreater(nav_at, real_body, f)
            self.assertGreater(nav_at, content.find("</head>"), f)

    def test_no_car_digest_placeholder(self):
        self._write("digest_2026-09-26.html", "<html><body>x</body></html>")
        website.build()
        cars_html = open(os.path.join(self.docs_dir, "cars.html"),
                         encoding="utf-8").read()
        self.assertIn("Not generated yet", cars_html)
        self.assertIn('href="index.html"', cars_html)


class TestDigestOutput(unittest.TestCase):
    def test_escaping_and_url_allowlist(self):
        evil = _car("ss.com", "e1", 3000)
        evil["url"] = "javascript:alert(1)"
        evil["title"] = "<script>x</script>"
        peers = [_car("pp.lv", f"p{i}", p)
                 for i, p in enumerate([4200, 4300, 4500, 4700])]
        qualified, assessed = car_value.score_and_rank([evil] + peers)
        html_text = car_digest.build_html(qualified, assessed, {}, {}, {},
                                          "2026-09-26")
        # The template's own column-sort script is the only <script> allowed;
        # the listing-controlled title must arrive escaped, not as markup
        # (an injected tag would make a second occurrence).
        self.assertEqual(html_text.count("<script>"), 1)
        self.assertNotIn("<script>x", html_text)
        self.assertNotIn("javascript:", html_text)


class TestBudgetTool(unittest.TestCase):
    """The in-browser custom-budget recomputation (embedded market JSON +
    JS port of the scorer)."""

    def _market(self):
        cand = _car("ss.com", "c1", 3000)
        peers = [_car("pp.lv", f"p{i}", p)
                 for i, p in enumerate([4200, 4300, 4500, 4700])]
        over = _car("pp.lv", "big", 6500)  # above ceiling: comparable only
        return [cand, over] + peers

    def _build(self):
        return car_digest.build_html([], [], {}, {}, {}, "2026-09-26",
                                     market=self._market())

    def test_market_embedded_with_config(self):
        html_text = self._build()
        m = re.search(r'<script type="application/json" id="car-market-data">'
                      r"(.*?)</script>", html_text, re.S)
        self.assertIsNotNone(m)
        payload = json.loads(m.group(1))
        self.assertEqual(payload["fields"][0], "source")
        self.assertEqual(len(payload["rows"]), 6)
        cfg = payload["config"]
        self.assertEqual(cfg["minPrice"], config.CAR_MIN_PRICE_EUR)
        self.assertEqual(cfg["compMax"], config.CAR_COMPARABLE_MAX_PRICE_EUR)
        self.assertEqual(cfg["minComps"], config.CAR_MIN_COMPARABLES)
        self.assertEqual(cfg["discountMult"], config.CAR_SCORE_DISCOUNT_MULTIPLIER)
        self.assertIn("url", payload["fields"])

    def test_budget_ui_present_with_market(self):
        html_text = self._build()
        self.assertIn("id='car-budget-input'", html_text)
        self.assertIn("id='car-budget-ok'", html_text)
        self.assertIn("id='car-budget-reset'", html_text)
        self.assertIn("id='car-custom-view'", html_text)
        self.assertIn("id=\"car-budget-js\"", html_text)
        # no market -> no tool (old callers keep working)
        plain = car_digest.build_html([], [], {}, {}, {}, "2026-09-26")
        self.assertNotIn("car-budget-input", plain)
        self.assertNotIn("car-market-data", plain)

    def test_embedded_urls_allowlisted(self):
        evil = _car("ss.com", "e1", 3000)
        evil["url"] = "javascript:alert(1)"
        html_text = car_digest.build_html([], [], {}, {}, {}, "2026-09-26",
                                         market=[evil] + self._market()[1:])
        m = re.search(r'id="car-market-data">(.*?)</script>', html_text, re.S)
        self.assertNotIn("javascript:", m.group(1))

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_js_scoring_matches_python(self):
        """Run the page's own JS under Node with a mini-DOM: the tool must
        initialize (this catches scripts that run before the body exists),
        apply() must render the custom view, and the scorer must produce
        the same qualified set/scores as car_value.score_and_rank."""
        html_text = self._build()
        payload = re.search(
            r'id="car-market-data">(.*?)</script>', html_text, re.S).group(1)
        js = re.search(
            r'<script id="car-budget-js">(.*?)</script>', html_text,
            re.S).group(1)
        driver = (
            "var fs = require('fs');\n"
            "function makeEl(extra) {\n"
            "  return Object.assign({style: {}, children: [], innerHTML: '',\n"
            "    addEventListener: function(){},\n"
            "    appendChild: function(c){this.children.push(c);},\n"
            "    setAttribute: function(){}, textContent: '', value: ''},\n"
            "    extra || {});\n"
            "}\n"
            "var elements = {\n"
            "  'car-market-data': {textContent:\n"
            "    fs.readFileSync(process.argv[3], 'utf8')},\n"
            "  'car-budget-input': makeEl({value: '3500'}),\n"
            "  'car-budget-status': makeEl(), 'car-default-view': makeEl(),\n"
            "  'car-custom-view': makeEl(), 'car-budget-ok': makeEl(),\n"
            "  'car-budget-reset': makeEl()};\n"
            "var parsed = false, readyCbs = [];\n"
            "global.document = {\n"
            "  get readyState() { return parsed ? 'complete' : 'loading'; },\n"
            "  getElementById: function(id) {\n"
            "    return parsed ? (elements[id] || null) : null; },\n"
            "  createElement: function(tag) { return makeEl(); },\n"
            "  createTextNode: function(t) { return {textContent: t}; },\n"
            "  addEventListener: function(ev, cb) {\n"
            "    if (ev === 'DOMContentLoaded') readyCbs.push(cb); }};\n"
            "global.window = {};\n"
            "eval(fs.readFileSync(process.argv[4], 'utf8'));\n"
            "// simulate the browser finishing <body> parsing\n"
            "parsed = true;\n"
            "readyCbs.forEach(function(cb) { cb(); });\n"
            "if (!global.window.__carBudget) {\n"
            "  console.log(JSON.stringify({error: 'init failed'}));\n"
            "  process.exit(0);\n"
            "}\n"
            "global.window.__carBudget.apply();\n"
            "var wiring = {children: elements['car-custom-view'].children.length,\n"
            "  defaultHidden: elements['car-default-view'].style.display,\n"
            "  customShown: elements['car-custom-view'].style.display};\n"
            "var res = global.window.__carBudget.compute(5000);\n"
            "console.log(JSON.stringify({wiring: wiring, deals:\n"
            "  res.map(function(x) { return {k: x.r[0] + ':' + x.r[1],\n"
            "    score: x.score, median: x.median, savings: x.savings}; })}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            drv = os.path.join(tmp, "driver.js")
            pay = os.path.join(tmp, "payload.txt")
            jsf = os.path.join(tmp, "budget.js")
            for path, text in ((drv, driver), (pay, payload), (jsf, js)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out = subprocess.run(["node", drv, "--", pay, jsf],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout.strip())
        # the tool actually initialized and apply() rendered the custom view
        self.assertGreaterEqual(data["wiring"]["children"], 3)
        self.assertEqual(data["wiring"]["defaultHidden"], "none")
        self.assertEqual(data["wiring"]["customShown"], "")
        js_rows = {r["k"]: r for r in data["deals"]}
        qualified, _assessed = car_value.score_and_rank(self._market())
        py_rows = {f"{l['source']}:{l['id']}": l for l in qualified}
        self.assertEqual(set(py_rows), set(js_rows))
        for key, l in py_rows.items():
            self.assertAlmostEqual(l["_score"], js_rows[key]["score"], delta=1,
                                   msg=key)
            self.assertAlmostEqual(l["_median"], js_rows[key]["median"],
                                   delta=0.01, msg=key)

    def test_history_html_days_trail_and_sparkline(self):
        d12 = (date.today() - timedelta(days=12)).isoformat()
        l = {"_first_seen": d12,
             "_price_hist": [["2026-09-01", 5500], ["2026-09-10", 4900]]}
        h = car_digest._history_html(l)
        self.assertIn("seen 12 d", h)
        self.assertIn("5,500", h)
        self.assertIn("→", h)
        self.assertIn("<svg", h)
        self.assertEqual(car_digest._history_html({}), "")
        self.assertIn("seen today",
                      car_digest._history_html(
                          {"_first_seen": date.today().isoformat()}))
        single = {"_price_hist": [["2026-09-01", 5500]]}
        self.assertNotIn("<svg", car_digest._history_html(single))

    def test_market_rows_carry_history_fields(self):
        l = _car("ss.com", "c1", 3000)
        l["_first_seen"] = "2026-09-20"
        l["_price_hist"] = [["2026-09-20", 5000], ["2026-09-26", 3000]]
        html_text = car_digest.build_html([], [], {}, {}, {}, "2026-09-26",
                                          market=[l])
        payload = json.loads(re.search(
            r'id="car-market-data">(.*?)</script>', html_text, re.S).group(1))
        self.assertIn("_first_seen", payload["fields"])
        self.assertIn("_price_hist", payload["fields"])
        row = payload["rows"][0]
        self.assertEqual(row[payload["fields"].index("_first_seen")],
                         "2026-09-20")
        self.assertEqual(row[payload["fields"].index("_price_hist")][-1],
                         ["2026-09-26", 3000])

    def test_filter_controls_present(self):
        html_text = self._build()
        for el in ("car-filter-make", "car-filter-model", "car-filter-fuel",
                   "car-filter-gearbox", "car-filter-year", "car-filter-km"):
            self.assertIn(f"id='{el}'", html_text)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_js_filters_narrow_candidates(self):
        """compute() must honour make/fuel/gearbox/year/km filters."""
        c1 = _car("ss.com", "c1", 3000)                      # vw passat diesel
        c2 = _car("ss.com", "c2", 3000, fuel="petrol", engine=1.6,
                  make="skoda", model="octavia-2", gearbox="automatic")
        peers = ([_car("pp.lv", f"d{i}", p)
                  for i, p in enumerate([4200, 4300, 4500, 4700])]
                 + [_car("pp.lv", f"g{i}", p, fuel="petrol", engine=1.6,
                         make="skoda", model="octavia-2", gearbox="automatic")
                    for i, p in enumerate([4200, 4300, 4500, 4700])])
        html_text = car_digest.build_html(
            [], [], {}, {}, {}, "2026-09-26", market=[c1, c2] + peers)
        payload = re.search(
            r'id="car-market-data">(.*?)</script>', html_text, re.S).group(1)
        js = re.search(
            r'<script id="car-budget-js">(.*?)</script>', html_text,
            re.S).group(1)
        driver = (
            "var fs = require('fs');\n"
            "function makeEl() { return {style:{}, children:[], innerHTML:'',\n"
            "  addEventListener:function(){},\n"
            "  appendChild:function(c){this.children.push(c);},\n"
            "  setAttribute:function(){}, textContent:'', value:''}; }\n"
            "var elements = {'car-market-data': {textContent:\n"
            "  fs.readFileSync(process.argv[3], 'utf8')}};\n"
            "var readyCbs = [];\n"
            "global.document = {readyState: 'loading',\n"
            "  getElementById: function(id){return elements[id] || null;},\n"
            "  createElement: function(){return makeEl();},\n"
            "  createTextNode: function(t){return {textContent:t};},\n"
            "  addEventListener: function(ev,cb){\n"
            "    if (ev==='DOMContentLoaded') readyCbs.push(cb);}};\n"
            "global.window = {};\n"
            "eval(fs.readFileSync(process.argv[4], 'utf8'));\n"
            "elements['car-budget-input'] = makeEl();\n"
            "elements['car-default-view'] = makeEl();\n"
            "elements['car-custom-view'] = makeEl();\n"
            "global.document.readyState = 'complete';\n"
            "global.document.getElementById = function(id){\n"
            "  return elements[id] || null;};\n"
            "readyCbs.forEach(function(cb){cb();});\n"
            "var B = global.window.__carBudget;\n"
            "function ids(list){return list.map(function(x){\n"
            "  return x.r[0] + ':' + x.r[1];});}\n"
            "console.log(JSON.stringify({\n"
            "  all: ids(B.compute(5000, {})),\n"
            "  diesel: ids(B.compute(5000, {fuel:'diesel'})),\n"
            "  skoda: ids(B.compute(5000, {make:'skoda'})),\n"
            "  year: ids(B.compute(5000, {minYear:2020})),\n"
            "  km: ids(B.compute(5000, {maxKm:100000})),\n"
            "  auto: ids(B.compute(5000, {gearbox:'automatic'})),\n"
            # 'Octavia' must match model 'octavia-2' (normalized compare)
            "  mOct: ids(B.compute(5000, {model:'Octavia'})),\n"
            "  mPass: ids(B.compute(5000, {model:'passat b7'}))\n"
            "}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            drv = os.path.join(tmp, "driver.js")
            pay = os.path.join(tmp, "payload.txt")
            jsf = os.path.join(tmp, "budget.js")
            for path, text in ((drv, driver), (pay, payload), (jsf, js)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out = subprocess.run(["node", drv, "--", pay, jsf],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout.strip())
        self.assertEqual(set(res["all"]), {"ss.com:c1", "ss.com:c2"})
        self.assertEqual(res["diesel"], ["ss.com:c1"])
        self.assertEqual(res["skoda"], ["ss.com:c2"])
        self.assertEqual(res["auto"], ["ss.com:c2"])
        self.assertEqual(res["year"], [])
        self.assertEqual(res["km"], [])
        self.assertEqual(res["mOct"], ["ss.com:c2"])
        self.assertEqual(res["mPass"], ["ss.com:c1"])

    def test_watchlist_ui_present(self):
        html_text = self._build()
        self.assertIn("id='car-watch-box'", html_text)
        self.assertIn("id='car-watch-list'", html_text)
        self.assertIn('id="car-watch-js"', html_text)
        self.assertIn("watch_cars_v1", html_text)
        # no market -> no watchlist either
        plain = car_digest.build_html([], [], {}, {}, {}, "2026-09-26")
        self.assertNotIn("car-watch-box", plain)

    def test_deal_rows_have_star_buttons(self):
        c = _car("ss.com", "c1", 3000)
        peers = [_car("pp.lv", f"p{i}", p)
                 for i, p in enumerate([4200, 4300, 4500, 4700])]
        qualified, assessed = car_value.score_and_rank([c] + peers)
        html_text = car_digest.build_html(qualified, assessed, {}, {},
                                          {}, "2026-09-26")
        self.assertIn("class='watch-star'", html_text)
        self.assertIn("data-key='ss.com:c1'", html_text)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_js_watchlist_toggle_and_status(self):
        """Star/unstar must persist in localStorage; a watched car missing
        from today's market must render as NO LONGER LISTED."""
        html_text = self._build()
        payload = re.search(
            r'id="car-market-data">(.*?)</script>', html_text, re.S).group(1)
        js = re.search(
            r'<script id="car-watch-js">(.*?)</script>', html_text,
            re.S).group(1)
        driver = (
            "var fs = require('fs');\n"
            "function makeEl() { return {style:{}, children:[], innerHTML:'',\n"
            "  addEventListener:function(){},\n"
            "  appendChild:function(c){this.children.push(c);},\n"
            "  insertBefore:function(c){this.children.unshift(c);},\n"
            "  setAttribute:function(){}, getAttribute:function(){return null;},\n"
            "  textContent:'', value:''}; }\n"
            "var store = {};\n"
            "global.localStorage = {\n"
            "  getItem: function(k){return store[k] || null;},\n"
            "  setItem: function(k,v){store[k]=v;}};\n"
            "var elements = {\n"
            "  'car-market-data': {textContent:\n"
            "    fs.readFileSync(process.argv[3], 'utf8')},\n"
            "  'car-watch-box': makeEl(), 'car-watch-list': makeEl(),\n"
            "  'car-watch-count': makeEl()};\n"
            "var readyCbs = [], clickCbs = [];\n"
            "global.document = {readyState: 'loading',\n"
            "  getElementById: function(id){return elements[id] || null;},\n"
            "  createElement: function(){return makeEl();},\n"
            "  createTextNode: function(t){return {textContent:t};},\n"
            "  addEventListener: function(ev,cb){\n"
            "    if (ev==='DOMContentLoaded') readyCbs.push(cb);\n"
            "    if (ev==='click') clickCbs.push(cb);}};\n"
            "global.window = {};\n"
            "eval(fs.readFileSync(process.argv[4], 'utf8'));\n"
            "global.document.readyState = 'complete';\n"
            "readyCbs.forEach(function(cb){cb();});\n"
            "function star(key) {\n"
            "  return {classList:{contains:function(c){return c==='watch-star';}},\n"
            "    getAttribute:function(k){return {\n"
            "      'data-key': key, 'data-label': 'Car ' + key,\n"
            "      'data-price': '3000', 'data-url': ''}[k];}};\n"
            "}\n"
            "clickCbs[0]({target: star('pp.lv:p1')});\n"
            "clickCbs[0]({target: star('ss.com:gone1')});\n"
            "var txt = JSON.stringify(elements['car-watch-list'].children);\n"
            "var stored = JSON.parse(store['watch_cars_v1'] || '{}');\n"
            "var ch = elements['car-watch-list'].children;\n"
            "var tbl = ch[ch.length - 1];\n"
            "console.log(JSON.stringify({\n"
            "  storedKeys: Object.keys(stored).sort(),\n"
            "  count: elements['car-watch-count'].textContent,\n"
            "  rows: tbl ? tbl.children.length : 0,\n"
            # p1 was starred at data-price 3000 but the embedded market
            # row carries 4200 -> a "since starred" delta must render.
            "  delta: txt.indexOf('since starred')\n"
            "}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            drv = os.path.join(tmp, "driver.js")
            pay = os.path.join(tmp, "payload.txt")
            jsf = os.path.join(tmp, "watch.js")
            for path, text in ((drv, driver), (pay, payload), (jsf, js)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out = subprocess.run(["node", drv, "--", pay, jsf],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout.strip())
        self.assertEqual(res["storedKeys"], ["pp.lv:p1", "ss.com:gone1"])
        self.assertEqual(res["count"], "(2)")
        self.assertEqual(res["rows"], 2)
        self.assertGreaterEqual(res["delta"], 0)

    def test_flat_budget_tool_embedded(self):
        listing = {"source": "ss.com", "id": "f1", "district": "Zolitude",
                   "rooms": 3, "area_m2": 55, "floor": "3/5",
                   "price_eur": 55000, "price_per_m2": 1000,
                   "_school_km": 0.8, "url": "https://www.ss.com/x"}
        all_scored = {"sale": [(listing, 1.23, "linear regression")]}
        html_text = notifier.build_html({}, {}, "", "note",
                                       all_scored=all_scored)
        self.assertIn('id="flat-listings-data"', html_text)
        self.assertIn("id='flat-budget-input'", html_text)
        self.assertIn("id='flat-budget-ok'", html_text)
        self.assertIn("id='flat-custom-view'", html_text)
        self.assertIn("id='flat-watch-box'", html_text)
        self.assertIn('id="flat-watch-js"', html_text)
        self.assertIn("watch_flats_v1", html_text)
        m = re.search(r'id="flat-listings-data">(.*?)</script>', html_text,
                      re.S)
        payload = json.loads(m.group(1))
        self.assertEqual(payload["config"]["minPrice"],
                         config.MIN_SALE_PRICE_EUR)
        self.assertEqual(len(payload["rows"]), 1)
        # no all_scored -> no tool (backward compatible)
        plain = notifier.build_html({}, {}, "", "note")
        self.assertNotIn("flat-budget-input", plain)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_flat_js_wiring_runs(self):
        """The flats budget JS must initialize after DOM-ready and render."""
        listing = {"source": "ss.com", "id": "f1", "district": "Zolitude",
                   "rooms": 3, "area_m2": 55, "floor": "3/5",
                   "price_eur": 55000, "price_per_m2": 1000,
                   "_school_km": 0.8, "url": "https://www.ss.com/x"}
        html_text = notifier.build_html(
            {}, {}, "", "note", all_scored={"sale": [(listing, 1.23, "x")]})
        payload = re.search(r'id="flat-listings-data">(.*?)</script>',
                            html_text, re.S).group(1)
        js = re.search(r'<script id="flat-budget-js">(.*?)</script>',
                       html_text, re.S).group(1)
        driver = (
            "var fs = require('fs');\n"
            "function makeEl(extra) {\n"
            "  return Object.assign({style: {}, children: [], innerHTML: '',\n"
            "    addEventListener: function(){},\n"
            "    appendChild: function(c){this.children.push(c);},\n"
            "    setAttribute: function(){}, textContent: '', value: ''},\n"
            "    extra || {});\n"
            "}\n"
            "var elements = {\n"
            "  'flat-listings-data': {textContent:\n"
            "    fs.readFileSync(process.argv[3], 'utf8')},\n"
            "  'flat-budget-input': makeEl({value: '60000'}),\n"
            "  'flat-budget-status': makeEl(), 'flat-custom-view': makeEl(),\n"
            "  'flat-budget-ok': makeEl(), 'flat-budget-reset': makeEl()};\n"
            "var parsed = false, readyCbs = [];\n"
            "global.document = {\n"
            "  get readyState() { return parsed ? 'complete' : 'loading'; },\n"
            "  getElementById: function(id) {\n"
            "    return parsed ? (elements[id] || null) : null; },\n"
            "  createElement: function(tag) { return makeEl(); },\n"
            "  createTextNode: function(t) { return {textContent: t}; },\n"
            "  addEventListener: function(ev, cb) {\n"
            "    if (ev === 'DOMContentLoaded') readyCbs.push(cb); }};\n"
            "global.window = {};\n"
            "eval(fs.readFileSync(process.argv[4], 'utf8'));\n"
            "parsed = true;\n"
            "readyCbs.forEach(function(cb) { cb(); });\n"
            "var result = {init: !!global.window.__flatBudget};\n"
            "if (global.window.__flatBudget) {\n"
            "  global.window.__flatBudget.apply();\n"
            "  result.children = elements['flat-custom-view'].children.length;\n"
            "  result.status = elements['flat-budget-status'].textContent;\n"
            "}\n"
            "console.log(JSON.stringify(result));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            drv = os.path.join(tmp, "driver.js")
            pay = os.path.join(tmp, "payload.txt")
            jsf = os.path.join(tmp, "budget.js")
            for path, text in ((drv, driver), (pay, payload), (jsf, js)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out = subprocess.run(["node", drv, "--", pay, jsf],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout.strip())
        self.assertTrue(result["init"])
        self.assertGreaterEqual(result["children"], 3)
        self.assertIn("1 of 1", result["status"])

    def test_flat_embed_extra_covers_unscored_page_rows(self):
        """Near-school rows come from all_listings, not all_scored — an
        unscored flat rendered on the page must land in payload.extra so
        the watchlist can still resolve it as 'still listed'."""
        scored = {"source": "ss.com", "id": "f1", "district": "Zolitude",
                  "rooms": 3, "area_m2": 55, "price_eur": 55000,
                  "url": "https://www.ss.com/x"}
        unscored = {"source": "ss.com", "id": "f2", "district": "Imanta",
                    "rooms": 1, "area_m2": 30, "price_eur": 40000,
                    "url": "https://www.ss.com/y"}
        html_text = notifier.build_html(
            {}, {}, "", "note",
            all_scored={"sale": [(scored, 1.0, "x")]},
            all_listings=[scored, unscored])
        payload = json.loads(re.search(
            r'id="flat-listings-data">(.*?)</script>', html_text,
            re.S).group(1))
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(len(payload["extra"]), 1)
        f = payload["fields"]
        ex = payload["extra"][0]
        self.assertEqual(ex[f.index("source")] + ":" + ex[f.index("id")],
                         "ss.com:f2")
        self.assertIsNone(ex[f.index("score")])
        # no all_listings -> extra is empty
        payload2 = json.loads(re.search(
            r'id="flat-listings-data">(.*?)</script>',
            notifier.build_html({}, {}, "", "note",
                                all_scored={"sale": [(scored, 1.0, "x")]}),
            re.S).group(1))
        self.assertEqual(payload2["extra"], [])

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_flat_watch_js_resolves_extra_rows(self):
        """A watched flat present only in payload.extra must render as
        'still listed', not 'NO LONGER LISTED'."""
        scored = {"source": "ss.com", "id": "f1", "district": "Zolitude",
                  "rooms": 3, "area_m2": 55, "price_eur": 55000,
                  "url": "https://www.ss.com/x"}
        unscored = {"source": "ss.com", "id": "f2", "district": "Imanta",
                    "rooms": 1, "area_m2": 30, "price_eur": 40000,
                    "url": "https://www.ss.com/y"}
        html_text = notifier.build_html(
            {}, {}, "", "note",
            all_scored={"sale": [(scored, 1.0, "x")]},
            all_listings=[scored, unscored])
        payload = re.search(r'id="flat-listings-data">(.*?)</script>',
                            html_text, re.S).group(1)
        js = re.search(r'<script id="flat-watch-js">(.*?)</script>',
                       html_text, re.S).group(1)
        driver = (
            "var fs = require('fs');\n"
            "function makeEl() { return {style:{}, children:[], innerHTML:'',\n"
            "  addEventListener:function(){},\n"
            "  appendChild:function(c){this.children.push(c);},\n"
            "  setAttribute:function(){}, getAttribute:function(){return null;},\n"
            "  textContent:'', value:''}; }\n"
            "var store = {};\n"
            "global.localStorage = {\n"
            "  getItem: function(k){return store[k] || null;},\n"
            "  setItem: function(k,v){store[k]=v;}};\n"
            "var elements = {\n"
            "  'flat-listings-data': {textContent:\n"
            "    fs.readFileSync(process.argv[3], 'utf8')},\n"
            "  'flat-watch-box': makeEl(), 'flat-watch-list': makeEl(),\n"
            "  'flat-watch-count': makeEl()};\n"
            "var readyCbs = [], clickCbs = [];\n"
            "global.document = {readyState: 'loading',\n"
            "  getElementById: function(id){return elements[id] || null;},\n"
            "  createElement: function(){return makeEl();},\n"
            "  createTextNode: function(t){return {textContent:t};},\n"
            "  addEventListener: function(ev,cb){\n"
            "    if (ev==='DOMContentLoaded') readyCbs.push(cb);\n"
            "    if (ev==='click') clickCbs.push(cb);}};\n"
            "global.window = {};\n"
            "eval(fs.readFileSync(process.argv[4], 'utf8'));\n"
            "global.document.readyState = 'complete';\n"
            "readyCbs.forEach(function(cb){cb();});\n"
            "clickCbs[0]({target:{classList:{contains:function(c){return c==='watch-star';}},\n"
            "  getAttribute:function(k){return {\n"
            "    'data-key':'ss.com:f2','data-label':'Flat f2',\n"
            "    'data-price':'40000','data-url':''}[k];}}});\n"
            "var txt = JSON.stringify(elements['flat-watch-list'].children);\n"
            "console.log(JSON.stringify({still: txt.indexOf('still listed'),\n"
            "  gone: txt.indexOf('NO LONGER')}));\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            drv = os.path.join(tmp, "driver.js")
            pay = os.path.join(tmp, "payload.txt")
            jsf = os.path.join(tmp, "watch.js")
            for path, text in ((drv, driver), (pay, payload), (jsf, js)):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            out = subprocess.run(["node", drv, "--", pay, jsf],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout.strip())
        self.assertGreaterEqual(res["still"], 0)
        self.assertEqual(res["gone"], -1)

    def test_market_stats_compute_groups_models(self):
        pool = ([_car("ss.com", f"a{i}", p, make="volkswagen",
                      model="passat-b7")
                 for i, p in enumerate([3000, 3500, 4000])]
                + [_car("pp.lv", "g1", 2500, make="volkswagen",
                        model="golf-6")]
                + [_car("ss.com", f"s{i}", p, make="skoda",
                        model="octavia")
                   for i, p in enumerate([5000, 5200, 5400, 5600])])
        stats = car_market.compute_market_stats(pool, [pool[0]])
        # golf-6 has 1 ad (< CAR_MARKET_MIN_LISTINGS) -> dropped
        self.assertEqual(len(stats), 2)
        passat = next(s for s in stats if s["model"] == "passat-b7")
        octavia = next(s for s in stats if s["model"] == "octavia")
        self.assertEqual(passat["ads"], 3)
        self.assertEqual(passat["median_price"], 3500)
        self.assertEqual(passat["min_price"], 3000)
        self.assertIn("/x/a0", passat["min_url"])
        self.assertEqual(passat["deals"], 1)
        self.assertEqual(octavia["deals"], 0)
        self.assertEqual(octavia["median_km"], 230000)
        # passat (1 deal) sorts before octavia (0)
        self.assertEqual(stats[0]["model"], "passat-b7")

    def test_market_page_render_and_placeholder(self):
        stats = [{"make": "Volkswagen", "model": "Passat B7", "ads": 5,
                  "median_price": 4000, "min_price": 3000,
                  "min_url": "https://www.ss.com/x/a0",
                  "median_year": 2012.0, "median_km": 230000.0,
                  "deals": 2}]
        html_text = car_market.build_market_html(stats, "2026-09-30", 100)
        self.assertIn("id='car-market'", html_text)
        self.assertIn("sortTable('car-market'", html_text)
        self.assertIn("Passat B7", html_text)
        self.assertIn("https://www.ss.com/x/a0", html_text)
        self.assertIn("Deals today", html_text)
        self.assertIn("cars.html?model=Passat%20B7", html_text)
        # missing stats file -> placeholder page
        with tempfile.TemporaryDirectory() as td:
            html2 = car_market.build_page(os.path.join(td, "none.json"))
        self.assertIn("Not generated yet", html2)

    def test_all_qualifying_header_and_no_relimit(self):
        qualified = [_car("ss.com", f"q{i}", 3000 + i) for i in range(30)]
        for l in qualified:
            l.update({"_score": 90, "_median": 4000, "_comps": 5,
                      "_savings": 1000, "_discount_pct": 25.0})
        html_text = car_digest.build_html(qualified, qualified, {}, {}, {},
                                          "2026-09-26")
        self.assertIn("All qualifying deals (30)", html_text)
        for l in qualified:
            self.assertIn(f"/x/{l['id']}", html_text)
        self.assertNotIn("⚠", html_text)

    def test_no_model_specific_watch_section(self):
        html_text = car_digest.build_html([], [], {}, {}, {}, "2026-09-26")
        self.assertNotIn("Passat B7 watch", html_text)
        self.assertNotIn("cannot be appraised", html_text)

    def test_pool_context_shown_under_median(self):
        good = _car("ss.com", "q1", 3000)
        good.update({"_score": 90, "_median": 4000, "_comps": 5,
                     "_savings": 1000, "_discount_pct": 25.0,
                     "_pool_year": 2012, "_pool_mileage": 240000})
        html_text = car_digest.build_html([good], [good], {}, {}, {},
                                          "2026-09-26")
        self.assertIn("pool ~2012 · ~240k km", html_text)

    def test_sortable_columns_wired(self):
        good = _car("ss.com", "q1", 3000)
        good.update({"_score": 90, "_median": 4000, "_comps": 5,
                     "_savings": 1000, "_discount_pct": 25.0,
                     "_pool_year": 2012, "_pool_mileage": 240000})
        html_text = car_digest.build_html([good], [good], {}, {}, {},
                                          "2026-09-26")
        self.assertIn("id='car-deals'", html_text)
        self.assertIn("sortTable('car-deals', 6)", html_text)
        self.assertEqual(html_text.count("class='sort-th'"), 7)
        self.assertIn("data-sort='volkswagen passat-b7'", html_text)
        self.assertIn("data-sort='3000'", html_text)
        self.assertIn("data-sort='4000'", html_text)
        self.assertIn("function sortTable", html_text)

    def test_inspection_cautions_displayed(self):
        old_high_km = _car("ss.com", "old", 3000, mileage=379000, year=2011)
        newer = _car("ss.com", "new", 3000, mileage=150000, year=2018)
        for l in (old_high_km, newer):
            l.update({"_score": 90, "_median": 4000, "_comps": 5,
                      "_savings": 1000, "_discount_pct": 25.0})
        html_text = car_digest.build_html([old_high_km, newer],
                                          [old_high_km, newer], {}, {}, {},
                                          "2026-09-26")
        self.assertIn("High mileage — budget for repairs", html_text)
        self.assertIn("Older car — inspect carefully", html_text)
        row_new = html_text[html_text.index("/x/new"):]
        self.assertNotIn("High mileage — budget for repairs", row_new)
        self.assertNotIn("Older car — inspect carefully", row_new)


class TestMainZeroFlats(unittest.TestCase):
    def test_cars_and_site_still_run_without_flats(self):
        with mock.patch.object(main.ss_com, "scrape", return_value=[]), \
             mock.patch.object(main.city24, "scrape", return_value=[]), \
             mock.patch.object(config, "IZSOLES_ENABLED", False), \
             mock.patch.object(config, "GEOCODE_ENABLED", False), \
             mock.patch.object(main.price_history, "update_price_history",
                               return_value={}), \
             mock.patch.object(main.health, "check"), \
             mock.patch.object(main.cars, "run",
                               return_value="cars ok") as cars_run, \
             mock.patch.object(main.website, "build") as site_build, \
             mock.patch.object(main, "_inject_chat"), \
             mock.patch.object(main.notifier, "save_digest") as save:
            msg = main.run()
        self.assertEqual(cars_run.call_count, 1)
        self.assertEqual(site_build.call_count, 1)
        save.assert_not_called()
        self.assertIn("0 listings", msg)


class TestArchivePruning(unittest.TestCase):
    def test_prunes_old_keeps_newest_and_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            digests = os.path.join(tmp, "digests")
            archive = os.path.join(tmp, "docs", "archive")
            os.makedirs(digests)
            os.makedirs(archive)
            names = ["digest_2026-07-01.html", "digest_2026-08-20.html",
                     "digest_2026-09-25.html", "digest_2026-09-26.html",
                     "cars_2026-07-15.html", "cars_2026-09-26.html",
                     "keep_me.txt"]
            for folder in (digests, archive):
                for n in names:
                    with open(os.path.join(folder, n), "w") as f:
                        f.write("x")
            with mock.patch.object(config, "DIGEST_DIR", digests), \
                 mock.patch.object(website, "ARCHIVE_DIR", archive), \
                 mock.patch.object(config, "ARCHIVE_KEEP_DAYS", 30):
                removed = website.prune_old_digests("2026-09-26")
            self.assertEqual(removed, 6)
            for folder in (digests, archive):
                left = sorted(os.listdir(folder))
                self.assertEqual(left, ["cars_2026-09-26.html",
                                        "digest_2026-09-25.html",
                                        "digest_2026-09-26.html",
                                        "keep_me.txt"])

    def test_newest_survives_even_if_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            digests = os.path.join(tmp, "digests")
            os.makedirs(digests)
            with open(os.path.join(digests, "cars_2026-01-01.html"), "w") as f:
                f.write("x")
            with mock.patch.object(config, "DIGEST_DIR", digests), \
                 mock.patch.object(website, "ARCHIVE_DIR",
                                   os.path.join(tmp, "none")), \
                 mock.patch.object(config, "ARCHIVE_KEEP_DAYS", 30):
                removed = website.prune_old_digests("2026-09-26")
            self.assertEqual(removed, 0)
            self.assertTrue(os.path.exists(
                os.path.join(digests, "cars_2026-01-01.html")))


if __name__ == "__main__":
    unittest.main()
