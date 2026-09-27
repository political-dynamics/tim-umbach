import copy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import collect_amrum as amrum


class AmrumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'data/amrum_market.json').read_text())

    def test_snapshot_is_reproducible_from_observations(self):
        self.assertEqual(self.data, amrum.recalculate(copy.deepcopy(self.data)))

    def test_target_prices_cannot_leak_into_market_estimates(self):
        changed = copy.deepcopy(self.data)
        for row in changed['observations']:
            if row['house'] == amrum.TARGET_HOUSE:
                row['nightly'] = 10000
        for flat in changed['apartments']:
            flat['tariffs'] = {'2026':[10000]*6, '2027':[10000]*6}
        amrum.recalculate(changed)
        self.assertEqual([a['market'] for a in self.data['apartments']],
                         [a['market'] for a in changed['apartments']])

    def test_peers_fit_capacity_location_and_independent_buildings(self):
        for flat in self.data['apartments']:
            peers = amrum.comparable_rows(flat, self.data['observations'])
            self.assertGreaterEqual(len(peers), 3)
            groups = [r.get('building',r['house']) for _,r in peers]
            self.assertEqual(len(groups),len(set(groups)))
            for _, row in peers:
                self.assertTrue(row['locality'].lower().startswith('nebel'))
                self.assertGreaterEqual(row['guests'],flat['guests'])
                self.assertNotEqual(row['house'],amrum.TARGET_HOUSE)

    def test_uncertainty_bands_and_seasons_are_ordered(self):
        for flat in self.data['apartments']:
            prices = flat['market']['seasons']
            self.assertGreaterEqual(prices['A']['estimate'],prices['B']['estimate'])
            self.assertGreaterEqual(prices['B']['estimate'],prices['C']['estimate'])
            for price in prices.values():
                self.assertLess(price['low'],price['estimate'])
                self.assertGreater(price['high'],price['estimate'])
                self.assertEqual(price['estimate'] % 5,0)

    def test_calendar_periods_are_contiguous_and_cross_year(self):
        for year in (2026,2027):
            periods = amrum.seasons(year)
            for first,second in zip(periods,periods[1:]):
                self.assertEqual(first['end'],second['start'])
            for p in periods:
                self.assertLess(date.fromisoformat(p['start']),date.fromisoformat(p['end']))
            self.assertEqual(date.fromisoformat(periods[-1]['end']).year,year+1)

    def test_ambiguous_target_year_is_not_silently_corrected(self):
        first = self.data['apartments'][0]
        self.assertIsNone(first['tariffs']['2027'])
        self.assertTrue(first['warning'])
        self.assertEqual(len(first['ambiguous_tariff']),6)

    def test_insufficient_peers_does_not_invent_a_price(self):
        self.assertIsNone(amrum.estimate(self.data['apartments'][0],[],self.data['seasonal_evidence']))

    def test_official_parser_separates_units_and_ignores_commented_prices(self):
        raw = '''<title>Example Nebel | Portal</title>
        <meta itemprop="latitude" content="54.652"><meta itemprop="longitude" content="8.35">
        <span itemprop="streetAddress">Example 1</span><span itemprop="addressLocality">Nebel/Amrum</span>
        <div class="tp-media" id="hp-1"><h3>Wohnung 1</h3>
        <!-- <span class="tp-price-amount">999,00 €</span> -->
        <span class="tp-price-amount">80,00 €</span><span class="tp-price-postfix">pro Einheit/ Nacht</span>Größe (Quadratmeter): 40
        Belegung: 1-4 Personen Endreinigung 50,- Euro</div>
        <div class="tp-media" id="hp-2"><h3>Wohnung 2</h3>Größe (Quadratmeter): 30</div>'''
        rows = amrum.parse_official(raw,'https://www.amrum.de/house/EXAMPLE','2026-09-27')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['nightly'],80)
        self.assertEqual(rows[0]['cleaning'],50)
        self.assertEqual(rows[0]['guests'],4)
        self.assertIsNone(rows[1]['nightly'])
        self.assertEqual(rows[1]['excluded'],'No public price')


if __name__ == '__main__':
    unittest.main()
