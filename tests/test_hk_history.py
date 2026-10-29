import unittest
from datetime import datetime,timedelta,timezone
from trading.hk_history import evaluate
from trading.policy import SafetyError

class HKHistory(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,9,tzinfo=timezone.utc)
        days=[(self.now.date()-timedelta(days=i)).isoformat() for i in reversed(range(60))]
        self.calendar=[dict(time=d,trade_date_type='WHOLE') for d in days]
        self.rows=[dict(date=d,open=80+i,close=80+i,high=81+i,low=79+i,volume=10000000,turnover=800000000) for i,d in enumerate(days)]
        self.fx=dict(verified=True,source='test fixture',as_of=self.now.isoformat(),hkd_per_usd=8)
    def call(self):return evaluate(self.rows,self.calendar,'2026-09-17',self.fx,self.now)
    def test_turnover_converted_to_dollars(self):
        self.assertTrue(self.call()['liquid'])
        for r in self.rows:r['turnover']=100000000
        self.assertFalse(self.call()['liquid'])
    def test_price_floor_is_dollars_and_signal_not_permission(self):
        r=self.call();self.assertTrue(r['buy']);self.assertFalse(r['orders_enabled'])
        for b in self.rows:
            for k in ('open','high','low','close'):b[k]/=10
        self.assertFalse(self.call()['liquid'])
    def test_gap_rejected(self):
        self.rows.pop(10)
        with self.assertRaises(SafetyError):self.call()
    def test_auction_not_yet_completed(self):
        self.now=self.now.replace(hour=8,minute=5)
        with self.assertRaises(SafetyError):self.call()
    def test_duplicate_calendar_rejected(self):
        self.calendar.insert(1,self.calendar[0])
        with self.assertRaises(SafetyError):self.call()
