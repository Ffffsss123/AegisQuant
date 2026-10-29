import tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from trading.us_budget_inputs import budget_inputs as us_inputs
from trading.hk_budget_inputs import budget_inputs as hk_inputs
from trading.simulation_loss_latch import initialize,persistent_budget_view

class CombinedBudget(unittest.TestCase):
    def test_three_sleeve_loss_and_no_cash_transfer(self):
        now=datetime.now(timezone.utc)
        def us(kind,cash,code,price,cost):
            state=dict(env='SIMULATE',account='8652',initial=500,budget_currency='USD',cash=cash,orders={},
                       positions={code:dict(qty=1,kind=kind,multiplier=100 if kind=='OPTION' else 1,cost=cost)})
            books={code:dict(bid=price,ask=price*1.001,bid_time=now.isoformat(),ask_time=now.isoformat())}
            return us_inputs(state,books,account='8652',kind=kind,now=now)
        hk=dict(as_of=now.isoformat(),account='8653',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,
                pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,
                committed_debit_usd='120',held_usd='0',exit_fee_reserve_usd='3',occupied_usd='123',realized_net_usd='-10',unrealized_net_usd='2')
        rows=dict(US_STOCK=us('STOCK',397,'US.A',100,100),US_OPTION=us('OPTION',295,'US.OPT',2,200),
                  HK=hk_inputs(hk,account='8653',portfolio='p',now=now))
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'risk.sqlite';initialize(p,'p',inherited_stop=False)
            r=persistent_budget_view(p,'p',rows,env='SIMULATE')
            self.assertEqual(r['combined_loss'],24)
            self.assertEqual(r['available'],dict(US_STOCK=394,US_OPTION=290,HK=367))
            hk['realized_net_usd']='-86'
            rows['HK']=hk_inputs(hk,account='8653',portfolio='p',now=now)
            self.assertTrue(persistent_budget_view(p,'p',rows,env='SIMULATE')['entry_block'])
            hk['realized_net_usd']='-10';rows['HK']=hk_inputs(hk,account='8653',portfolio='p',now=now)
            self.assertEqual(sum(persistent_budget_view(p,'p',rows,env='SIMULATE')['available'].values()),0)
