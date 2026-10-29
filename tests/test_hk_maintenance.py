import unittest
from pathlib import Path
from unittest.mock import patch
from trading.hk_maintenance import strategy
from trading.policy import SafetyError
class HKMaintenance(unittest.TestCase):
    def test_separate_unbound_simulator_allowed_without_binding(self):
        state=dict(env='SIMULATE',purpose='strategy_simulation',portfolio_id='p',account='8652')
        with patch('trading.hk_maintenance.ledger',return_value=state):
            self.assertEqual(strategy(Path('/test'),'8653'),state)
            self.assertNotIn('hk_account',state)
    def test_wrong_environment_account_or_binding_rejected(self):
        base=dict(env='SIMULATE',purpose='strategy_simulation',portfolio_id='p',account='8652')
        for change in [dict(env='REAL'),dict(account='8653'),dict(hk_account='wrong'),dict(portfolio_id='')]:
            with self.subTest(change=change),patch('trading.hk_maintenance.ledger',return_value=dict(base,**change)):
                with self.assertRaises(SafetyError):strategy(Path('/test'),'8653')

class HKMaintenanceShutdown(unittest.TestCase):
    def test_own_snapshot_invalidated_but_other_account_untouched(self):
        import tempfile,json
        from trading.hk_maintenance import mark_stopped
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'data/trading';data.mkdir(parents=True)
            p=data/'hk_risk_snapshot.json';report=data/'hk_runtime.report.json'
            p.write_text(json.dumps(dict(account='8653',reconciled=True)))
            report.write_text(json.dumps(dict(background_running=True,status='reconciled')))
            mark_stopped(root,'8653')
            self.assertFalse(p.exists());self.assertFalse(json.loads(report.read_text())['background_running'])
            p.write_text(json.dumps(dict(account='another')))
            mark_stopped(root,'8653');self.assertTrue(p.exists())
