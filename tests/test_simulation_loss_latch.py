import sqlite3,tempfile,unittest
from pathlib import Path
from trading.simulation_loss_latch import initialize,retain_loss_stop
from trading.policy import SafetyError

class LossLatch(unittest.TestCase):
    def test_restart_and_reinitialization_cannot_clear_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'risk.sqlite';initialize(p,'portfolio',inherited_stop=False)
            self.assertFalse(retain_loss_stop(p,'portfolio',env='SIMULATE',triggered=False))
            self.assertTrue(retain_loss_stop(p,'portfolio',env='SIMULATE',triggered=True))
            with self.assertRaises(FileExistsError):initialize(p,'portfolio',inherited_stop=False)
            self.assertTrue(retain_loss_stop(p,'portfolio',env='SIMULATE',triggered=False))
    def test_missing_wrong_identity_and_real_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'risk.sqlite'
            with self.assertRaises(sqlite3.OperationalError):retain_loss_stop(p,'portfolio',env='SIMULATE',triggered=False)
            self.assertFalse(p.exists());initialize(p,'portfolio',inherited_stop=False)
            for owner,env,trigger in [('other','SIMULATE',False),('portfolio','REAL',False),('portfolio','SIMULATE',0)]:
                with self.assertRaises(SafetyError):retain_loss_stop(p,owner,env=env,triggered=trigger)

    def test_migration_inherits_existing_loss_stop(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'risk.sqlite'
            for invalid in (None,0,'false'):
                with self.assertRaises(SafetyError):
                    initialize(p,'portfolio',inherited_stop=invalid)
                self.assertFalse(p.exists())
            initialize(p,'portfolio',inherited_stop=True)
            self.assertTrue(retain_loss_stop(p,'portfolio',env='SIMULATE',triggered=False))

    def test_budget_result_persists_stop_before_return(self):
        from trading.simulation_loss_latch import persistent_budget_view
        from trading.simulation_budget import SLEEVES
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'risk.sqlite';initialize(p,'portfolio',inherited_stop=False)
            rows={k:dict(initial=500,cash=500,equity=500,deployed=0,reserved=0,exit_fees=0) for k in SLEEVES}
            rows['HK']['equity']=400
            self.assertTrue(persistent_budget_view(p,'portfolio',rows,env='SIMULATE')['entry_block'])
            rows['HK']['equity']=500
            self.assertEqual(sum(persistent_budget_view(p,'portfolio',rows,env='SIMULATE')['available'].values()),0)
            with self.assertRaises(sqlite3.OperationalError):
                persistent_budget_view(Path(d)/'missing.sqlite','portfolio',rows,env='SIMULATE')
