import unittest
from unittest.mock import Mock,patch
from trading.option_transport import verify_initial_account
from trading.policy import SafetyError

class ResetAccount(unittest.TestCase):
    def test_archived_terminal_orders_and_unknown_rejection(self):
        state=dict(env='SIMULATE',account='8652',baseline={},positions={},orders={},bound_at='2026-09-21T12:00:00+00:00')
        stock=dict(state,acceptance_reset_id='r',historical_order_manifest='/archive/known.json')
        row=dict(id='old',code='US.A',side='SELL',status='FILLED_ALL',qty=1,filled=1,average=12)
        archive=dict(env='SIMULATE',account='8652',sealed=True,orders=[row],history_start='2026-09-01')
        b=Mock();b.orders.return_value=[row];b.positions.return_value={}
        with patch('trading.simulation_history.load_reset_history',return_value=archive):
            self.assertEqual(verify_initial_account(b,stock,state),{'old'})
            b.orders.assert_called_with('2026-09-01')
            b.orders.return_value=[row,dict(row,id='unknown')]
            with self.assertRaises(SafetyError):verify_initial_account(b,stock,state)
            b.orders.return_value=[row];b.positions.return_value={'US.A':1}
            with self.assertRaises(SafetyError):verify_initial_account(b,stock,state)
