import unittest
from datetime import datetime,timedelta,timezone
from trading.hk_policy import execution_session,board_order,purchase_plan,HK
from trading.policy import SafetyError

class HKPolicy(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,10,tzinfo=HK)
        self.rows=[dict(time='2026-09-17',trade_date_type='WHOLE')]
        self.fx=dict(hkd_per_usd='7.8',verified=True,source='test_fixture_not_market_rate',as_of=self.now.isoformat())
    def test_open_lunch_close_auction(self):
        for h,m,yes in [(9,30,False),(9,35,True),(11,59,True),(12,0,False),(12,59,False),(13,0,True),(15,59,True),(16,0,False)]:
            self.assertEqual(bool(execution_session(self.rows,self.now.replace(hour=h,minute=m))),yes)
    def test_half_day_holiday_timezone(self):
        self.rows[0]['trade_date_type']='MORNING'
        self.assertIsNone(execution_session(self.rows,self.now.replace(hour=13)))
        self.assertIsNone(execution_session([],self.now))
        self.assertEqual(execution_session(self.rows,self.now.astimezone(timezone.utc)),'2026-09-17')
        with self.assertRaises(SafetyError):execution_session(self.rows,self.now.replace(tzinfo=None))
    def test_unknown_and_duplicate_calendar_rejected(self):
        with self.assertRaises(SafetyError):execution_session(self.rows*2,self.now)
        self.rows[0]['trade_date_type']='OTHER'
        with self.assertRaises(SafetyError):execution_session(self.rows,self.now)
    def test_lots_tick_and_nonfinite(self):
        self.assertEqual(board_order('HK.00001',200,'10.02',100,'.02'),2004)
        for args in [('HK.00001',1,10,100,.02),('US.A',100,10,100,.02),('HK.00001',100,10.01,100,.02),('HK.00001',100,'NaN',100,.02)]:
            with self.assertRaises(SafetyError):board_order(*args)
    def plan(self,**kw):
        args=dict(code='HK.00001',price=10,lot_size=100,tick_size=.02,available_usd=200,cash_hkd=10000,roundtrip_fee_hkd=50,fx=self.fx,now=self.now);args.update(kw)
        return purchase_plan(**args)
    def test_shared_cash_does_not_use_nominal_account_cash(self):
        r=self.plan();self.assertEqual(r['qty'],100);self.assertFalse(r['orders_enabled'])
        self.assertEqual(self.plan(available_usd=100)['qty'],0)
        self.assertEqual(self.plan(cash_hkd=900)['qty'],0)
    def test_fx_and_fee_required(self):
        for fx in [{},dict(self.fx,verified=False),dict(self.fx,as_of=(self.now-timedelta(days=2)).isoformat()),dict(self.fx,as_of=(self.now+timedelta(seconds=1)).isoformat())]:
            with self.assertRaises(SafetyError):self.plan(fx=fx)
        with self.assertRaises(SafetyError):self.plan(roundtrip_fee_hkd=0)
