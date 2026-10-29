import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from trading.external_news import collect_external
from trading.news import assess


class HKNewsObservation(unittest.TestCase):
    def collect(self, codes, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sources.json'
            path.write_text(json.dumps(dict(
                feeds=[dict(id='official', required=True)],
                x=dict(enabled=False, required=False), entities={})))
            return collect_external(codes, path, clock=lambda: self.now,
                                    fetch=lambda spec: [], **kwargs)

    def setUp(self):
        self.now = datetime(2026, 9, 17, 3, tzinfo=timezone.utc)

    def test_hk_missing_coverage_does_not_break_us_collection(self):
        snapshot = self.collect(['US.AAPL', 'HK.00700'])
        self.assertTrue(snapshot['health']['US.AAPL']['ok'])
        self.assertFalse(snapshot['health']['HK.00700']['ok'])
        self.assertEqual(assess(snapshot, 'US.AAPL', self.now)['multiplier'], 1)
        self.assertEqual(assess(snapshot, 'HK.00700', self.now)['multiplier'], 0)

    def test_alias_alone_cannot_claim_hk_coverage(self):
        snapshot = self.collect(['HK.00700'], additional_entities={'HK.00700': ['Tencent']})
        self.assertFalse(snapshot['health']['HK.00700']['ok'])
        self.assertEqual(snapshot['health']['HK.00700']['status'], 'hk_issuer_coverage_not_validated')

    def test_reject_ambiguous_or_unsupported_market_codes(self):
        for code in ['700', 'HK.700', 'hk.00700', 'HK.007000', 'HK.００７００', 'SG.D05', None]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.collect([code])

    def test_held_and_pending_hk_remain_watched_without_candidates(self):
        from trading.desktop_news import watched_codes
        state = dict(positions={'HK.00700': {'qty': 100}, 'HK.00001': {'qty': 0}},
                     orders={'open': dict(code='HK.00941', status='SUBMITTED'),
                             'done': dict(code='HK.00005', status='FILLED_ALL'),
                             'bad': dict(code=None, status='SUBMITTED')})
        with patch('trading.desktop_news.read_json', return_value={}), patch('trading.desktop_news.ledger', return_value=state):
            self.assertEqual(watched_codes(), ['HK.00700', 'HK.00941', 'US.AAPL', 'US.SPY'])

    def test_invalid_candidates_cannot_break_shared_collector(self):
        from trading.desktop_news import watched_codes
        scan = {'candidates': [{'code': c} for c in [None, 'HK.700', 'HK.００７００', 'HK.00700', 'US.MSFT']]}
        with patch('trading.desktop_news.read_json', return_value=scan), patch('trading.desktop_news.ledger', return_value={}):
            self.assertEqual(watched_codes(), ['HK.00700', 'US.AAPL', 'US.MSFT', 'US.SPY'])

    def test_hk_headline_maps_verified_exact_alias_without_us_ticker_fabrication(self):
        from trading.external_news import analyze
        spec=dict(id='rthk_finance',tier='newsroom',article_hosts=['news.rthk.hk'])
        row=dict(title='Tencent announces acquisition',summary='',url='https://news.rthk.hk/article',
                 source='RTHK',publish_time=self.now.isoformat())
        result=analyze(row,spec,self.now,{'HK.00700':['Tencent'],'HK.700':['Tencent']})
        self.assertEqual(result['codes'],['HK.00700'])
        self.assertEqual(result['action'],'observe')
        row['title']='Tencentish announces acquisition'
        self.assertEqual(analyze(row,spec,self.now,{'HK.00700':['Tencent']})['codes'],[])
