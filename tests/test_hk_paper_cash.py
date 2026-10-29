import unittest
from datetime import datetime,timezone
from trading.hk_account import validate_paper_cash
from trading.policy import SafetyError

class HKPaperCashTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc);stamp=self.now.isoformat()
        self.accounts=[dict(acc_id='8653',trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')]
        self.row=dict(currency='N/A',cash=999000,frozen_cash=100,total_assets=1000000,market_val=1000,interest_charged_amount=0)
        self.snapshot=dict(account='8653',env='SIMULATE',checked_at=stamp,funds=dict(return_code=0,response=[self.row]))
        self.inventory=dict(account='8653',inventory_reconciled=True,as_of=stamp,positions={'HK.00001':'100'})
    def validate(self):return validate_paper_cash(self.accounts,self.snapshot,self.inventory,account='8653',now=self.now)
    def test_hk_virtual_cash_never_adds_usd_capital(self):
        result=self.validate();self.assertEqual(result['cash_cap_hkd'],'998900')
        self.assertEqual(result['shared_budget_credit_usd'],'0');self.assertFalse(result['real_settled_cash_verified'])
    def test_negative_debt_currency_or_asset_mismatch_rejected(self):
        for field,value in [('cash',-1),('interest_charged_amount',1),('currency','USD'),('total_assets',1001000)]:
            old=self.row[field];self.row[field]=value
            with self.assertRaises(SafetyError):self.validate()
            self.row[field]=old
    def test_position_value_and_query_failure_rejected(self):
        self.inventory['positions']={}
        with self.assertRaises(SafetyError):self.validate()
        self.inventory['positions']={'HK.00001':'100'};self.snapshot['funds']['return_code']=-1
        with self.assertRaises(SafetyError):self.validate()
    def test_real_or_other_account_cannot_use_simulator_fallback(self):
        self.accounts[0]['trd_env']='REAL'
        with self.assertRaises(SafetyError):self.validate()
        self.accounts[0]['trd_env']='SIMULATE';self.inventory['account']='7929'
        with self.assertRaises(SafetyError):self.validate()
