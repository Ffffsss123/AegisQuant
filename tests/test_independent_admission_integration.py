"""Composition checks before independent execution is enabled."""
import unittest,tempfile
from pathlib import Path
from decimal import Decimal
from trading.simulation_loss_latch import initialize,persistent_budget_view
from trading.simulation_slots import ledger_slots,require_capacity
from trading.policy import SafetyError

class IndependentAdmission(unittest.TestCase):
    def test_loss_across_sleeves_survives_recovery_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'risk.sqlite';initialize(path,'portfolio',inherited_stop=False)
            sleeves={k:dict(initial=500,cash=500,equity=500,deployed=0,reserved=0,exit_fees=0) for k in ('US_STOCK','US_OPTION','HK')}
            sleeves['US_OPTION'].update(cash=410,equity=450,reserved=100,exit_fees=5,deployed=85)
            sleeves['HK']['equity']=450
            stopped=persistent_budget_view(path,'portfolio',sleeves,env='SIMULATE')
            self.assertEqual(stopped['combined_loss'],100)
            self.assertTrue(stopped['entry_block'])
            self.assertTrue(all(v==0 for v in stopped['available'].values()))
            for s in sleeves.values():s['equity']=550
            # Each call opens a fresh SQLite connection, simulating a restarted caller.
            self.assertTrue(persistent_budget_view(path,'portfolio',sleeves,env='SIMULATE')['entry_block'])
    def test_pending_cross_market_options_share_slots_but_not_stock_slots(self):
        ledgers={
            'us':dict(env='SIMULATE',account='8652',positions={'US.O':dict(qty=1,kind='OPTION')},orders={}),
            'hk':dict(env='SIMULATE',account='8653',positions={},orders={'pending':dict(code='HK.O',kind='OPTION',side='BUY',status='UNKNOWN')})}
        slots=ledger_slots(ledgers)
        with self.assertRaises(SafetyError):require_capacity(slots,'OPTION',env='SIMULATE')
        self.assertEqual(require_capacity(slots,'US_STOCK',env='SIMULATE')['OPTION'],2)
        self.assertEqual(require_capacity(slots,'HK_STOCK',env='SIMULATE')['HK_STOCK'],0)
