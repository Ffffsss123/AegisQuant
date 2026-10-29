import unittest
from datetime import datetime,timezone,timedelta
from trading.hk_portfolio import aggregate
from trading.policy import SafetyError

class CrossMarketRisk(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,tzinfo=timezone.utc);ts=self.now.isoformat()
        self.base=dict(env='SIMULATE',currency='USD',portfolio_id='fixture',account='us',hk_account='hk',as_of=ts,reconciled=True,budget_usd=500,loss_limit_usd=100,available_usd=300,combined_equity_usd=490,pending_or_unknown=False)
        self.hk=dict(env='SIMULATE',currency='HKD',portfolio_id='fixture',account='hk',as_of=ts,reconciled=True,position_cost_hkd=780,position_value_hkd=702,buy_reservation_hkd=78,exit_fee_reserve_hkd=39,fees_hkd=39,realized_pnl_hkd=0,pending_or_unknown=False,broker_cash_hkd=1000000)
        self.fx=dict(as_of=ts,verified=True,source='fixture',hkd_per_usd='7.8')
    def run_risk(self):return aggregate(self.base,self.hk,self.fx,self.now)
    def test_only_hk_pnl_is_added_not_nominal_cash(self):
        r=self.run_risk();self.assertEqual(float(r['combined_equity_usd']),475)
        self.assertEqual(float(r['available_usd']),180);self.assertFalse(r['orders_enabled'])
    def test_loss_and_unknown_orders_block_new_admission(self):
        self.base['combined_equity_usd']=415
        self.assertTrue(self.run_risk()['loss_stop']);self.assertEqual(float(self.run_risk()['available_usd']),0)
        self.base['combined_equity_usd']=490;self.hk['pending_or_unknown']=True
        self.assertTrue(self.run_risk()['reconciliation_block']);self.assertEqual(float(self.run_risk()['available_usd']),0)
    def test_identity_reconciliation_and_stale_fail_closed(self):
        for field,value in [('env','REAL'),('account','us'),('portfolio_id','other'),('reconciled',False),('as_of',(self.now-timedelta(seconds=31)).isoformat())]:
            original=self.hk[field];self.hk[field]=value
            with self.assertRaises(SafetyError):self.run_risk()
            self.hk[field]=original
    def test_no_reserve_money_or_unverified_fx(self):
        self.base['budget_usd']=890
        with self.assertRaises(SafetyError):self.run_risk()
        self.base['budget_usd']=500;self.fx['verified']=False
        with self.assertRaises(SafetyError):self.run_risk()
    def test_negative_nonfinite_and_missing_fail_closed(self):
        for value in (-1,'NaN',None):
            self.hk['buy_reservation_hkd']=value
            with self.assertRaises(SafetyError):self.run_risk()

    def test_currency_and_both_accounts_are_explicit(self):
        for record,key in [(self.base,'account'),(self.base,'currency'),(self.hk,'currency')]:
            saved=record.pop(key)
            with self.assertRaises(SafetyError):self.run_risk()
            record[key]=saved
        self.hk['currency']='USD'
        with self.assertRaises(SafetyError):self.run_risk()
