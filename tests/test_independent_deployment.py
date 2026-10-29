import unittest
from datetime import datetime,timezone
from trading.independent_deployment import targets
from trading.policy import SafetyError

class Deployment(unittest.TestCase):
    def test_preserves_controls_history_and_counts_only_three_execution_budgets(self):
        now=datetime.now(timezone.utc)
        s=dict(account='000001',env='SIMULATE',purpose='strategy_simulation',strategy_version='swing_1_5_v1',
            portfolio_id='p',acceptance_reset_id='reset',initial=500,cash=500,reserve_usd=390,loss_limit=100,
            paused=True,stopped=True,alerts=['manual'],orders={},positions={},baseline={},queue=[],fx=.6,
            budget_currency='USD',option_budget_usd=500)
        observer=dict(env='QUOTE_PAPER',portfolio_id='p',position=None,trades=[],cash=500,initial=500,paused=True,alerts=['keep'])
        args=dict(account='000001',hk_account='000002',history=dict(account='000002',directory='/verified',raw_sha256='hash',history_start='2026-09-21'),fx_reference=dict(verified=True,source='fixture',as_of=now.isoformat(),hkd_per_usd=8),now=now,migration_id='m')
        out=targets(s,None,None,observer,**args)
        self.assertEqual(sum(out[k]['initial'] for k in ('US_STOCK','US_OPTION','HK')),1500)
        self.assertEqual(out['OBSERVER']['execution_budget_usd'],0)
        self.assertEqual(out['US_STOCK']['alerts'],['manual']);self.assertEqual(out['US_STOCK']['reserve_usd'],390)
        self.assertTrue(all(out[k]['paused'] and out[k]['stopped'] for k in ('US_STOCK','US_OPTION','HK')))
        self.assertNotIn('capital_mode',s)
        with self.assertRaises(SafetyError):targets(s,{},None,observer,**args)
        s['cash']=499
        with self.assertRaises(SafetyError):targets(s,None,None,observer,**args)
