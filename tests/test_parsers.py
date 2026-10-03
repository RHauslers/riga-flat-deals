# -*- coding: utf-8 -*-
"""Parser fixture tests — feed saved/synthetic HTML through each source's
parse function and assert the extracted fields. Fully offline: no network,
no Playwright. If a site changes its markup, the fixture test that fails
pinpoints which parser broke.

Fixtures live in tests/fixtures/. Note: .gitignore blocks `ss_*.html` and
`city24_*.html`, so ss.com fixtures are named `sscom_*.html` on purpose.
"""
import json
import os
import unittest

from bs4 import BeautifulSoup

from scrapers import ss_com, car_ss, car_pp, izsoles, city24

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def _rows(html):
    return BeautifulSoup(html, "lxml").find_all("tr")


class TestSsComFlats(unittest.TestCase):

    def test_row_to_listing_extracts_fields(self):
        trs = _rows(_fixture("sscom_today.html"))
        item = ss_com._row_to_listing(trs[0], "sale")
        self.assertEqual(item["source"], "ss.com")
        self.assertEqual(item["id"], "1001")
        self.assertEqual(item["district"], "Imanta")
        self.assertEqual(item["street"], "Anniņmuižas bulv. 10")
        self.assertEqual(item["rooms"], 2)
        self.assertEqual(item["area_m2"], 45)
        self.assertEqual(item["floor_num"], 3)
        self.assertEqual(item["floor_total"], 5)
        self.assertEqual(item["price_eur"], 50000)
        self.assertEqual(item["price_per_m2"], 1111)
        self.assertEqual(item["ad_slug"], "abcde")
        self.assertTrue(item["url"].startswith("https://www.ss.com/msg/"))

    def test_day_price_unit_detected(self):
        trs = _rows(_fixture("sscom_today.html"))
        item = ss_com._row_to_listing(trs[1], "sale")
        self.assertEqual(item["district"], "Zolitude")  # alias zolitūde
        self.assertEqual(item["price_unit"], "day")

    def test_non_target_district_filtered(self):
        trs = _rows(_fixture("sscom_today.html"))
        self.assertIsNone(ss_com._row_to_listing(trs[2], "sale"))


class TestSsComCars(unittest.TestCase):

    def test_row_to_listing_extracts_fields(self):
        trs = _rows(_fixture("sscom_cars.html"))
        item = car_ss._row_to_listing(trs[0])
        self.assertEqual(item["source"], "ss.com")
        self.assertEqual(item["id"], "9001")
        self.assertEqual(item["make"], "audi")
        self.assertEqual(item["model"], "a4")
        self.assertEqual(item["year"], 2012)
        self.assertEqual(item["engine_l"], 2.0)
        self.assertEqual(item["fuel"], "diesel")
        self.assertEqual(item["mileage_km"], 254000)
        self.assertEqual(item["price_eur"], 5900)
        self.assertEqual(item["gearbox"], "automatic")
        self.assertEqual(item["body"], "wagon")

    def test_buy_ad_filtered(self):
        trs = _rows(_fixture("sscom_cars.html"))
        self.assertIsNone(car_ss._row_to_listing(trs[1]))  # "Pērk" title

    def test_decimal_thousands_mileage_and_petrol(self):
        trs = _rows(_fixture("sscom_cars.html"))
        item = car_ss._row_to_listing(trs[2])
        self.assertEqual(item["mileage_km"], 185500)  # '185,5 tūkst.'
        self.assertEqual(item["fuel"], "petrol")      # plain '1.6'
        self.assertEqual(item["gearbox"], "manual")   # 'mehāniskā' in title
        self.assertEqual(item["body"], "hatchback")   # 'hečbeks' in title

    def test_row_without_tr_id_skipped(self):
        trs = _rows(_fixture("sscom_cars.html"))
        self.assertIsNone(car_ss._row_to_listing(trs[3]))


class TestPpLvCars(unittest.TestCase):

    def test_parse_page_cards(self):
        items = car_pp.parse_page(_fixture("pp_lv_cards.html"))
        # fixture: 1 valid audi, 1 duplicate id, 1 buy-ad, 1 valid vw
        self.assertEqual(len(items), 2)
        a4 = next(i for i in items if i["id"] == "12345")
        self.assertEqual(a4["source"], "pp.lv")
        self.assertEqual(a4["make"], "audi")
        self.assertEqual(a4["model"], "a4")
        self.assertEqual(a4["year"], 2012)
        self.assertEqual(a4["mileage_km"], 254000)
        self.assertEqual(a4["engine_l"], 2.0)
        self.assertEqual(a4["fuel"], "diesel")
        self.assertEqual(a4["gearbox"], "automatic")
        self.assertEqual(a4["body"], "wagon")
        self.assertEqual(a4["price_eur"], 5900)
        self.assertEqual(a4["location"], "Rīga")
        golf = next(i for i in items if i["id"] == "24680")
        self.assertEqual(golf["fuel"], "petrol")
        self.assertEqual(golf["gearbox"], "manual")
        self.assertEqual(golf["body"], "hatchback")


class TestIzsoles(unittest.TestCase):

    def test_list_page_items_and_pagination(self):
        items, max_page = izsoles._parse_list_page(
            _fixture("izsoles_list.html"))
        self.assertEqual(len(items), 1)  # Rīga-only, deduped
        self.assertIn("Rīga", items[0]["title"])
        self.assertIn("/izsole/", items[0]["url"])
        self.assertEqual(max_page, 3)

    def test_detail_extracts_auction_fields(self):
        item = izsoles._parse_detail(
            _fixture("izsoles_detail.html"),
            "https://izsoles.ta.gov.lv/izsole/11111111-2222-3333-4444-555555555555",
            "Anniņmuižas bulvārs 10 - 5, Rīga")
        self.assertEqual(item["source"], "izsoles.ta.gov.lv")
        self.assertEqual(item["id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(item["auction_start_price"], 31900)
        self.assertEqual(item["auction_current_bid"], 33500)
        self.assertEqual(item["auction_appraisal"], 40000)
        self.assertEqual(item["auction_end"], "2026-10-15")
        self.assertEqual(item["auction_register_until"], "2026-10-10")
        self.assertEqual(item["rooms"], 2)       # 'divistabu' word form
        self.assertEqual(item["area_m2"], 50.1)  # 'platību 50,1 m2' — land skipped
        self.assertEqual(item["price_eur"], 33500)  # current bid wins
        self.assertIsNone(item["ownership_share"])
        self.assertEqual(item["street"], "Anniņmuižas bulvārs 10")

    def test_detail_ownership_share(self):
        item = izsoles._parse_detail(
            _fixture("izsoles_detail.html"),
            "https://izsoles.ta.gov.lv/izsole/abcdefab-1234-1234-1234-abcdefabcdef",
            "1/2 domājamā daļa no Anniņmuižas bulvārs 10 - 5, Rīga")
        self.assertEqual(item["ownership_share"], "1/2")


class TestCity24(unittest.TestCase):

    def test_extract_item(self):
        with open(os.path.join(FIXTURES, "city24_items.json"),
                  encoding="utf-8") as f:
            items = json.load(f)
        out = city24._extract_item(items[0], "sale")
        self.assertEqual(out["source"], "city24.lv")
        self.assertEqual(out["district"], "Imanta")
        self.assertEqual(out["street"], "Anniņmuižas bulvāris 10")
        self.assertEqual(out["floor"], "3/5")
        self.assertEqual(out["floor_num"], 3)
        self.assertEqual(out["price_eur"], 50000)
        self.assertEqual(out["price_per_m2"], 1111.11)
        self.assertIn("ab12cd", out["url"])
        self.assertAlmostEqual(out["lat"], 56.9496)

    def test_non_target_district_filtered(self):
        with open(os.path.join(FIXTURES, "city24_items.json"),
                  encoding="utf-8") as f:
            items = json.load(f)
        self.assertIsNone(city24._extract_item(items[1], "sale"))  # Centrs

    def test_missing_area_filtered(self):
        with open(os.path.join(FIXTURES, "city24_items.json"),
                  encoding="utf-8") as f:
            items = json.load(f)
        self.assertIsNone(city24._extract_item(items[2], "sale"))


if __name__ == "__main__":
    unittest.main()
