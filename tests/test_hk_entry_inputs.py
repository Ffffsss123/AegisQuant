import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from trading.hk_order_request import entry_inputs
from trading.policy import SafetyError
class HKEntryInputs(unittest.TestCase):
    def setUp(self):
        now=datetime(2026,9,18,2,tzinfo=timezone.utc)
        self.args=dict(root='.',portfolio_id='p',account='8653',accounts=[dict(acc_id='8653',trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')],account_checked_at=now.isoformat(),calendar=[dict(time='2026-09-18',trade_date_type='WHOLE')],now=now,scan={},catalog={},snapshot={},snapshot_checked_at=now.isoformat(),code='HK.00001',book=dict(bid=10,ask=10.01,bid_time=now.isoformat(),ask_time=now.isoformat()))
        self.signals=dict(session='2026-09-18',candidates=[dict(code='HK.00001')])
        self.meta=dict(code='HK.00001',verified=True,suspended=False,kind='COMMON',date='2026-09-18',lot_size=100,tick_size='.01')
    def build(self,**changes):
        with patch('trading.hk_signals.current_signals',return_value=self.signals),patch('trading.hk_metadata.execution_metadata',return_value=self.meta):
            return entry_inputs(**dict(self.args,**changes))
    def test_restart_identity_stable_and_portfolio_specific(self):
        a=self.build();b=self.build();self.assertEqual(a['tag'],b['tag']);self.assertEqual(a['qty'],100)
        self.assertNotEqual(a['tag'],self.build(portfolio_id='other')['tag'])
    def test_no_candidate_cannot_make_request(self):
        self.signals['candidates']=[]
        with self.assertRaises(SafetyError):self.build()
    def test_wide_buy_spread_still_rejected(self):
        with self.assertRaises(SafetyError):self.build(book=dict(self.args['book'],ask=11))
    def test_wrong_account_environment_blocked(self):
        self.args['accounts'][0]['trd_env']='REAL'
        with self.assertRaises(SafetyError):self.build()
