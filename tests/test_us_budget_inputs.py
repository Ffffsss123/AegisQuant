import unittest
from datetime import datetime,timezone,timedelta
from trading.us_budget_inputs import budget_inputs
from trading.policy import SafetyError

class Inputs(unittest.TestCase):
    def test_fresh_bid_and_exit_fee_and_stale_rejection(self):
        now=datetime.now(timezone.utc)
        s=dict(env='SIMULATE',account='8652',initial=500,budget_currency='USD',cash=397,orders={},positions={'US.A':dict(qty=1,kind='STOCK',multiplier=1,cost=100)})
        b={'US.A':dict(bid=110,ask=120,bid_time=now.isoformat(),ask_time=now.isoformat())}
        r=budget_inputs(s,b,account='8652',kind='STOCK',now=now)
        self.assertEqual(r['equity'],504);self.assertEqual(r['exit_fees'],3)
        with self.assertRaises(SafetyError):budget_inputs(s,b,account='8652',kind='STOCK',now=now+timedelta(seconds=31))
        s['orders']['x']=dict(status='UNKNOWN')
        with self.assertRaises(SafetyError):budget_inputs(s,b,account='8652',kind='STOCK',now=now)

    def test_option_multiplier_spread_and_invalid_inventory(self):
        now=datetime.now(timezone.utc)
        s=dict(env='SIMULATE',account='8652',initial=500,budget_currency='USD',cash=295,orders={},positions={'US.OPT':dict(qty=1,kind='OPTION',multiplier=100,cost=200)})
        b={'US.OPT':dict(bid=2.10,ask=2.15,bid_time=now.isoformat(),ask_time=now.isoformat())}
        self.assertEqual(budget_inputs(s,b,account='8652',kind='OPTION',now=now)['equity'],500)
        b['US.OPT']['ask']=2.50
        with self.assertRaises(SafetyError):budget_inputs(s,b,account='8652',kind='OPTION',now=now)
        b['US.OPT']['ask']=2.15
        for qty in (True,0.5,-1):
            s['positions']['US.OPT']['qty']=qty
            with self.assertRaises(SafetyError):budget_inputs(s,b,account='8652',kind='OPTION',now=now)

    def test_reconciled_partial_fill_loss_observation_keeps_entry_closed(self):
        now=datetime.now(timezone.utc)
        state=dict(env='SIMULATE',account='a',initial=500,budget_currency='USD',cash=397,
            positions={'US.A':dict(qty=1,kind='STOCK',multiplier=1,cost=100)},
            orders={'x':dict(id='known',status='FILLED_PART',kind='STOCK',side='BUY',
                multiplier=1,qty=2,booked_qty=1,price=100,fee_reserve=3,booked_fee=3)})
        books={'US.A':dict(bid=1,ask=1.001,bid_time=now.isoformat(),ask_time=now.isoformat())}
        result=budget_inputs(state,books,account='a',kind='STOCK',now=now,for_loss_observation=True)
        self.assertEqual(result['equity'],395)
        self.assertEqual(result['reserved'],100)
        with self.assertRaises(SafetyError):budget_inputs(state,books,account='a',kind='STOCK',now=now)
        state['orders']['x']['status']='UNKNOWN'
        with self.assertRaises(SafetyError):budget_inputs(state,books,account='a',kind='STOCK',now=now,for_loss_observation=True)
