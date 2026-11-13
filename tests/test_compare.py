import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import app
from normalize.match import parse_listing_title as parse, match_score

def listing(title="Dolo 650mg Tablet 15's", price=30, stock=True):
    return SimpleNamespace(raw_title=title, pack_label=None, selling_price=price,
                           in_stock=stock, rx_required=True, product_url='https://www.1mg.com/test')

class MatchingTests(unittest.TestCase):
    def test_equivalent_formatting(self):
        self.assertEqual(match_score(parse("Dolo 650mg Tablet 15's"), parse('DOLO 650mg Strip of 15 Tablets')), 1)
    def test_different_identities_never_match(self):
        pairs = [
            ("Dolo 650 Tablet 15's", "Dolo 650mg Tablet 15's"),
            ("Vitamin B12 10mg Tablet 15's", "Vitamin B6 10mg Tablet 15's"),
            ("Dolo 650mg Tablet 15's", "Dolo 500mg Tablet 15's"),
            ("Dolo 10mg Tablet 15's", "Dolo 10mcg Tablet 15's"),
            ("Dolo 650mg Tablet 15's", "Dolo 650mg Capsule 15's"),
            ("Dolo 650mg Tablet 15's", "Dolo 650mg Tablet 10's"),
            ("Dolo 650mg Tablet 15's", "Dolo SR 650mg Tablet 15's"),
            ("Dolo 650mg Tablet 15's", "Dolo D 650mg Tablet 15's"),
            ("Dolo 650mg Tablet 15's", "Dolo 650mg Tablet"),
            ("Example 10mg Cream 15's", "Example 10mg Gel 15's"),
            ("Example 10mg Syrup 100ml", "Example 10mg Suspension 100ml"),
            ("Example 10mg/5ml Syrup 100ml", "Example 10mg/10ml Syrup 100ml"),
            ("Example 10mg + 5mg Tablet 15's", "Example 10mg + 2mg Tablet 15's"),
        ]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertLess(match_score(parse(left), parse(right)), .8)
    def test_volume_is_not_strength(self):
        parsed = parse('Example 10mg Syrup 100ml')
        self.assertEqual(parsed.strength, '10mg')
        self.assertEqual(parsed.pack_size, '100ml')
    def test_unit_conversion(self):
        self.assertEqual(match_score(parse("Example 1g Tablet 10's"), parse("Example 1000mg Tablet 10's")), 1)

class CompareTests(unittest.TestCase):
    def compare(self, one, pharma, net):
        with patch.object(app.scrape_1mg, 'search', side_effect=one if isinstance(one, Exception) else None, return_value=one), patch.object(app.scrape_pharmeasy, 'search', return_value=pharma), patch.object(app.scrape_netmeds, 'search', return_value=net):
            return app.run_compare('Dolo 650')
    def test_unavailable_cheapest_is_excluded(self):
        result = self.compare([listing(price=1, stock=False)], [listing(price=20)], [listing(price=30)])
        self.assertEqual(result['cheapest']['platform'], 'PharmEasy')
    def test_failed_provider_does_not_hide_others(self):
        result = self.compare(RuntimeError('upstream failed'), [listing()], [])
        self.assertIsNotNone(result['reference'])
        self.assertEqual(result['platforms']['1mg']['status'], 'error')
        self.assertEqual(result['cheapest']['platform'], 'PharmEasy')
    def test_invalid_prices_are_excluded(self):
        for price in [0, -1, math.nan, math.inf, 'bad', None]:
            with self.subTest(price=price):
                self.assertIsNone(self.compare([listing(price=price)], [], [])['cheapest'])
    def test_complete_reference_preferred_to_unknown_unit(self):
        result = self.compare([listing(title="Dolo 650 Tablet 15's")], [listing()], [])
        self.assertEqual(result['reference'], "Dolo 650mg Tablet 15's")
        self.assertEqual(len(result['platforms']['1mg']['matched']), 0)
        self.assertEqual(result['cheapest']['platform'], 'PharmEasy')
    def test_reference_fallback_preserves_requested_strength(self):
        result = self.compare([listing(title="Dolo 650 Tablet 15's")], [listing(title="Dolo 500mg Tablet 15's")], [])
        self.assertEqual(result['reference'], "Dolo 650 Tablet 15's")
        self.assertIsNone(result['cheapest'])
    def test_no_listings(self):
        self.assertIsNone(self.compare([], [], [])['reference'])
    def test_duplicate_pack_is_not_appended(self):
        row = listing()
        row.pack_label = "15's"
        self.assertEqual(app.full_text(row), row.raw_title)
    def test_whitespace_query_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            app.compare('   ')
    def test_numeric_rupee_price(self):
        self.assertEqual(app.scrape_1mg._parse_rupees(32.5), 32.5)


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(app.app)
        app._CACHE.clear()
    def test_health_and_home(self):
        self.assertEqual(self.client.get('/health').json(), {'ok': True})
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/static/index.html').status_code, 200)
    def test_query_validation(self):
        for query in ['', ' ', 'a', 'x' * 121]:
            self.assertEqual(self.client.get('/api/compare', params={'q': query}).status_code, 422)
    def test_cache_and_outage_retry(self):
        good = {'platforms': {'1mg': {'error': None}}}
        with patch.object(app, 'run_compare', return_value=good) as run:
            self.client.get('/api/compare?q=Dolo%20650')
            self.client.get('/api/compare?q=dolo%20%20650')
            self.assertEqual(run.call_count, 1)
        app._CACHE.clear()
        with patch.object(app, 'run_compare', return_value={'platforms': {'1mg': {'error': 'unavailable'}}}) as run:
            self.client.get('/api/compare?q=Dolo%20650')
            self.client.get('/api/compare?q=Dolo%20650')
            self.assertEqual(run.call_count, 2)
