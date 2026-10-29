import unittest
from datetime import datetime,timezone,timedelta
from trading.hk_budget_inputs import budget_inputs
from trading.policy import SafetyError

class Inputs(unittest.TestCase):
    def test_fee_mapping_and_fail_closed_snapshot(self):
        now=datetime.now(timezone.utc)
        r=dict(as_of=now.isoformat(),account='8653',portfolio_id='p',env='SIMULATE',currency='USD',
               reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,
               committed_debit_usd='120',held_usd='0',exit_fee_reserve_usd='3',occupied_usd='123',
               realized_net_usd='-10',unrealized_net_usd='2')
        v=budget_inputs(r,account='8653',portfolio='p',now=now)
        self.assertEqual(v['cash'],370);self.assertEqual(v['equity'],492)
        self.assertEqual(v['deployed'],120);self.assertEqual(v['exit_fees'],3)
        for patch in [dict(occupied_usd='126'),dict(account='other'),dict(env='REAL'),dict(entry_block=True),
                      dict(pending_or_unknown=True),dict(as_of=(now-timedelta(seconds=31)).isoformat())]:
            with self.subTest(patch=patch),self.assertRaises(SafetyError):
                budget_inputs(dict(r,**patch),account='8653',portfolio='p',now=now)
