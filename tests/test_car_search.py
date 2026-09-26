# -*- coding: utf-8 -*-
"""Car-search tests: scraper row/card parsing, cross-source dedupe,
comparable-listing scoring, digest/site building, and cars.run() failure
behaviour. All file writes happen in tempfile dirs — repo data/ and docs/
are never touched."""
import json
import os
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
import cars
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
        os.makedirs(self.digest_dir)
        self._patches = [
            mock.patch.object(config, "DIGEST_DIR", self.digest_dir),
            mock.patch.object(config, "CAR_SEEN_JSON", self.seen_json),
            mock.patch.object(config, "CAR_MARKET_SNAPSHOT_JSON",
                              self.snapshot_json),
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
        self.assertNotIn("<script>", html_text)
        self.assertNotIn("javascript:", html_text)

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
