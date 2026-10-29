import unittest
from datetime import datetime,timezone,timedelta
from trading.hk_account import validate_snapshot
from trading.policy import SafetyError

class HKAccount(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,tzinfo=timezone.utc)
        self.accounts=[dict(acc_id='test8653',trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'])]
        self.funds=[dict(hk_cash=1000,hk_avl_withdrawal_cash=800,hkd_net_cash_power=900,cash=1000000,power=2000000,currency='N/A')]
    def check(self,**kw):
        args=dict(account='test8653',captured_at=self.now.isoformat(),now=self.now);args.update(kw)
        return validate_snapshot(self.accounts,self.funds,**args)
    def test_uses_conservative_explicit_hkd_cap(self):
        r=self.check();self.assertEqual(r['cash_cap_hkd'],'800');self.assertFalse(r['orders_enabled'])
    def test_no_aggregate_or_margin_fallback(self):
        for field in ('hk_cash','hk_avl_withdrawal_cash','hkd_net_cash_power'):
            saved=self.funds[0][field];self.funds[0][field]='N/A'
            with self.assertRaises(SafetyError):self.check()
            self.funds[0][field]=saved
    def test_account_identity_and_environment(self):
        for k,v in [('trd_env','REAL'),('acc_status','DISABLED'),('trdmarket_auth',['HK','US'])]:
            saved=self.accounts[0][k];self.accounts[0][k]=v
            with self.assertRaises(SafetyError):self.check()
            self.accounts[0][k]=saved
        with self.assertRaises(SafetyError):self.check(account='other')
        self.accounts*=2
        with self.assertRaises(SafetyError):self.check()
    def test_stale_future_missing_and_negative(self):
        for seconds in (-31,1):
            with self.assertRaises(SafetyError):self.check(captured_at=(self.now+timedelta(seconds=seconds)).isoformat())
        self.funds[0]['hk_cash']=-1
        with self.assertRaises(SafetyError):self.check()
    def test_zero_cash_is_valid_but_no_capacity(self):
        self.funds[0]['hk_avl_withdrawal_cash']=0
        self.assertEqual(self.check()['cash_cap_hkd'],'0')

class HKFlatPaperBaseline(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,tzinfo=timezone.utc)
        self.accounts=[dict(acc_id='test8653',trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')]
        self.row=dict(currency='N/A',cash=1000000,total_assets=1000000,frozen_cash=0,market_val=0,interest_charged_amount=0)
        self.snapshot=dict(checked_at=self.now.isoformat(),funds=dict(return_code=0,response=[self.row]),positions=dict(return_code=0,response=[]),orders=dict(return_code=0,response=[]))
    def check(self):
        from trading.hk_account import validate_flat_paper_snapshot
        return validate_flat_paper_snapshot(self.accounts,self.snapshot,account='test8653',now=self.now)
    def test_virtual_baseline_never_credits_shared_budget(self):
        r=self.check()
        self.assertEqual(r['virtual_cash_hkd'],'1000000')
        self.assertEqual(r['shared_budget_credit_usd'],'0')
        self.assertFalse(r['orders_enabled']);self.assertFalse(r['real_settled_cash_verified'])
    def test_real_margin_options_and_multimarket_rejected(self):
        for field,value in [('trd_env','REAL'),('acc_type','MARGIN'),('sim_acc_type','OPTION'),('trdmarket_auth',['US','HK'])]:
            old=self.accounts[0][field];self.accounts[0][field]=value
            with self.assertRaises(SafetyError):self.check()
            self.accounts[0][field]=old
    def test_query_failure_or_nonempty_baseline_rejected(self):
        for name in ('funds','positions','orders'):
            self.snapshot[name]['return_code']=-1
            with self.assertRaises(SafetyError):self.check()
            self.snapshot[name]['return_code']=0
        for name in ('positions','orders'):
            self.snapshot[name]['response']=[{'fixture':'unknown'}]
            with self.assertRaises(SafetyError):self.check()
            self.snapshot[name]['response']=[]
    def test_cash_discrepancies_currency_and_staleness_rejected(self):
        for field,value in [('cash','NaN'),('cash',-1),('currency','USD'),('total_assets',999999),('frozen_cash',1),('market_val',1),('interest_charged_amount',1)]:
            old=self.row[field];self.row[field]=value
            with self.assertRaises(SafetyError):self.check()
            self.row[field]=old
        self.snapshot['checked_at']=(self.now-timedelta(seconds=31)).isoformat()
        with self.assertRaises(SafetyError):self.check()
