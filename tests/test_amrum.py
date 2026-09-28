import copy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import collect_amrum as amrum
import amrum_model
import amrum_history


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

    def test_checked_main_tariffs_are_displayed_with_year_provenance(self):
        first = self.data['apartments'][0]
        self.assertEqual(first['tariffs']['2027'], [81,112,162,112,81,112])
        self.assertIsNone(first['warning'])
        self.assertIn('inferred', first['tariff_note'])
        for flat in self.data['apartments']:
            self.assertIsNotNone(flat['tariff_checked_at'])

    def test_model_uses_broad_sample_and_building_weights(self):
        rows = amrum_model.training_rows(self.data['observations'], amrum.TARGET_HOUSE)
        self.assertGreater(len(rows),1000)
        self.assertEqual(len(rows),self.data['model']['observation_count'])
        totals = {}
        for row,weight in zip(rows,amrum_model.weights(rows)):
            group = amrum_model.building(row)
            totals[group] = totals.get(group,0) + weight
        for total in totals.values():
            self.assertAlmostEqual(total,1.)
        self.assertEqual(len(totals),self.data['model']['property_count'])
        self.assertLess(self.data['model']['validation']['mae_eur'],
                        self.data['model']['validation']['baseline_mae_eur'])

    def test_duplicate_ids_and_target_address_are_excluded(self):
        rows = copy.deepcopy(self.data['observations'])
        original = amrum_model.training_rows(rows, amrum.TARGET_HOUSE)
        rows.append(copy.deepcopy(original[0]))
        target = next(r for r in rows if r['house'] == amrum.TARGET_HOUSE)
        leak = dict(original[0], id='leak', house='OTHER', building=target['building'], nightly=9999)
        rows.append(leak)
        self.assertEqual(original,amrum_model.training_rows(rows,amrum.TARGET_HOUSE))

    def test_fitted_effect_recovers_synthetic_area_relationship(self):
        rows = [dict(id=str(i),house=str(i),area=25+i,guests=2,bedrooms=1,
                     locality='Nebel',nightly=3*(25+i),cleaning=None) for i in range(50)]
        fitted = amrum_model.fit(rows,.001)
        prediction = __import__('math').exp(amrum_model.predict_log(fitted,dict(rows[0],area=40)))
        self.assertAlmostEqual(prediction,120,delta=1)

    def test_year_dummy_is_tested_on_independent_owners(self):
        effects = self.data['year_effects']
        self.assertEqual(effects['2025']['provider_count'],1)
        self.assertFalse(effects['2025']['accepted'])
        self.assertEqual(effects['2027']['provider_count'],3)
        self.assertFalse(effects['2027']['accepted'])
        self.assertGreater(effects['2027']['validation']['year_dummy_log_rmse'],
                           effects['2027']['validation']['no_year_dummy_log_rmse'])
        rows = [dict(provider=str(p),apartment='1',source_season=s,year=y,
                     nightly=100 if y==2026 else 110) for p in range(4) for s in 'ABC' for y in (2026,2027)]
        effect = amrum_history.year_effects(rows)['2027']
        self.assertTrue(effect['accepted'])
        self.assertAlmostEqual(effect['supported_factor'],1.1)
        self.assertEqual(effect['applied_factor'],1.)

    def test_history_preserves_native_seasons_and_unknown_fee_years(self):
        rows = self.data['historical_tariffs']
        self.assertEqual(len(rows),98)
        self.assertEqual({r['year'] for r in rows},{2025,2026,2027})
        sanskiin = [r for r in rows if r['provider']=='sanskiin']
        self.assertEqual({r['source_season'] for r in sanskiin},set('ABCD'))
        self.assertTrue(all(r['mandatory_stay_fee'] is None for r in sanskiin))
        self.assertEqual(self.data['seasonal_coverage']['independent_tariff_providers'],5)

    def test_insufficient_peers_does_not_invent_a_price(self):
        self.assertIsNone(amrum.estimate(self.data['apartments'][0],[],self.data['seasonal_evidence']))

    def test_history_cannot_change_current_price_estimates(self):
        changed = copy.deepcopy(self.data)
        for row in changed['historical_tariffs']:
            if row['year'] != 2026:
                row['nightly'] *= 10
        changed['history_series'] = {'periods':[{'median_nightly':99999}]}
        amrum.recalculate(changed)
        self.assertEqual([a['market'] for a in changed['apartments']],
                         [a['market'] for a in self.data['apartments']])

    def test_historical_index_matches_units_and_deduplicates_months(self):
        current = [dict(id='one',house='one',area=40,guests=2,bedrooms=1,nightly=100,excluded=None),
                   dict(id='two',house='two',area=40,guests=2,bedrooms=1,nightly=200,excluded=None)]
        old = [dict(current[0],nightly=50,captured_at='2025-02-01T00:00:00Z'),
               dict(current[0],nightly=80,captured_at='2025-02-15T00:00:00Z'),
               dict(current[1],nightly=160,captured_at='2025-02-02T00:00:00Z'),
               dict(current[0],id='absent',nightly=1,captured_at='2025-02-02T00:00:00Z')]
        series=amrum_history.monthly_series(current,old,'2026-09-27T00:00:00Z',amrum.TARGET_HOUSE)
        self.assertEqual(series['periods'][0]['apartments'],2)
        self.assertEqual(series['periods'][0]['matched_index'],80.)
        self.assertEqual(series['periods'][1]['matched_index'],100.)
        old[1]['area']=60
        series=amrum_history.monthly_series(current,old,'2026-09-27T00:00:00Z',amrum.TARGET_HOUSE)
        self.assertEqual(series['periods'][0]['matched_apartments'],1)

    def test_target_parser_ignores_mobile_tables_and_guards_year_inference(self):
        def table(prices, dates):
            return '<table class="preis-table"><tbody><tr>'+''.join(f'<td>{p},-*</td>' for p in prices)+'</tr></tbody><tfoot>'+''.join(f'<td>{d}</td>' for d in dates)+'</tfoot></table>'
        dates = ['09.01.-27.02.', '27.02.-12.06.', '12.06.-11.09.',
                 '11.09.-30.10.', '30.10.-18.12.', '18.12.-08.01.']
        raw = '<h4>2026</h4>'+table([77,107,157,107,77,107],dates)
        raw += table([999,999,999],dates[:3])
        raw += '<h4>2026</h4>'+table([81,112,162,112,81,112],dates)
        parsed = amrum.parse_target(raw,1,self.data['apartments'][0])
        self.assertEqual(parsed['tariffs']['2027'],[81,112,162,112,81,112])
        changed = amrum.parse_target(raw.replace('09.01.-27.02.','10.01.-28.02.'),1,self.data['apartments'][0])
        self.assertIsNone(changed['tariffs']['2027'])

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
