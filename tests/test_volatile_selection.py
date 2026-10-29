import unittest
from tests.test_trading import bars
from trading.swing_model import features,snapshot,SELECTION_PROFILE,size
from trading.policy import SafetyError

class VolatileSelection(unittest.TestCase):
    def data(self):
        b=bars()
        for i,row in enumerate(b):
            price=20+i*.2
            row.update(open=price,close=price,high=price+.1,low=price-1)
        return b
    def test_clear_trend_and_volatility_pass(self):
        b=self.data();f=features(b,b[-1]['date'])
        self.assertTrue(f['volatile_trend']);self.assertTrue(f['buy'])
        self.assertGreaterEqual(f['atr_pct'],.02)
    def test_extreme_volatility_excluded_but_exit_signals_retained(self):
        b=self.data()
        for row in b:row['low']=row['close']-5
        scan=dict(date=b[-1]['date'],generated_at='x',complete=True,signals={'US.A':{}},candidates=[dict(code='US.A')])
        result=snapshot(scan,{'US.A':b},SELECTION_PROFILE)
        self.assertTrue(result['complete']);self.assertEqual(result['candidates'],[]);self.assertIn('sell',result['signals']['US.A'])
    def test_low_volatility_and_choppy_trend_excluded(self):
        b=self.data()
        for row in b:row['low']=row['close']-.1
        self.assertFalse(features(b,b[-1]['date'])['volatile_trend'])
        b=self.data()
        for i,row in enumerate(b):
            p=30+(i%2)*2+i*.01;row.update(open=p,close=p,high=p+.1,low=p-.1)
        self.assertLess(features(b,b[-1]['date'])['trend_efficiency'],.30)
        self.assertFalse(features(b,b[-1]['date'])['volatile_trend'])
    def test_verified_universe_ties_and_unknown_profile(self):
        b=self.data();scan=dict(date=b[-1]['date'],generated_at='x',complete=True,signals={'US.B':{},'US.A':{},'US.X':{}},candidates=[dict(code='US.B'),dict(code='US.A')])
        r=snapshot(scan,{c:b for c in scan['signals']},SELECTION_PROFILE)
        self.assertEqual([c['code'] for c in r['candidates']],['US.A','US.B'])
        with self.assertRaises(SafetyError):snapshot(scan,{},'typo')
    def test_higher_volatility_does_not_increase_risk_budget(self):
        low=size(500,500,10,dict(stop_distance=1,target_distance=2))
        high=size(500,500,10,dict(stop_distance=3,target_distance=6))
        self.assertLessEqual(high,low)
