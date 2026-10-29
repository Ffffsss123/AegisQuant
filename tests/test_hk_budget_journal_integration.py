import tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from trading.hk_reservations import Reservations
from trading.hk_budget_inputs import budget_inputs

class JournalBudget(unittest.TestCase):
    def test_settlement_to_budget_preserves_fees_and_closed_pnl(self):
        with tempfile.TemporaryDirectory() as d:
            journal=Reservations(Path(d)/'hk.sqlite','p')
            journal.reserve('entry',130,500)
            journal.settle_fill('entry',debit_usd=120,filled_qty=100,broker_status='FILLED_ALL',reconciled=True,evidence='test broker fill')
            now=datetime.now(timezone.utc)
            meta=dict(as_of=now.isoformat(),account='8653',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,
                      entry_block=False,budget_usd=500,loss_limit_usd=100)
            totals=journal.risk_totals({'entry':dict(market_value_usd=125,exit_fee_reserve_usd=3)})
            inputs=budget_inputs(dict(meta,**totals),account='8653',portfolio='p',now=now)
            self.assertEqual(inputs['cash'],380);self.assertEqual(inputs['equity'],502)
            journal.settle_closed_position('entry',net_proceeds_usd=122,sold_qty=100,order_ids=['sell'],
                position_flat=True,no_pending_orders=True,reconciled=True,evidence='test closed fill')
            inputs=budget_inputs(dict(meta,**journal.risk_totals({})),account='8653',portfolio='p',now=now)
            self.assertEqual(inputs['cash'],502);self.assertEqual(inputs['equity'],502)
            self.assertEqual(inputs['deployed'],0);self.assertEqual(inputs['exit_fees'],0)
            journal.close()
