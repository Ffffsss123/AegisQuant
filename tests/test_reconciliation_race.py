import unittest
from tests import test_trading as h

class Races(unittest.TestCase):
    setUp=h.Orders.setUp
    tearDown=h.Orders.tearDown
    def test_fill_between_order_and_position_snapshots_rechecks_once(self):
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW)
        original=self.b.positions;calls=[]
        def positions():
            if not calls:self.b.fill(1,10)
            calls.append(1);return original()
        self.b.positions=positions
        self.e.reconcile(self.s,h.NOW)
        self.assertFalse(self.s['paused']);self.assertEqual(self.s['positions']['US.A']['qty'],1);self.assertEqual(len(calls),2)
    def test_persistent_mismatch_still_pauses(self):
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW);self.b.pos['US.A']=1
        self.e.reconcile(self.s,h.NOW);self.assertTrue(self.s['paused']);self.assertEqual(self.s['positions']['US.A']['qty'],0)
    def test_cancel_races_with_full_fill(self):
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW)
        def cancel(oid):self.b.fill(1,10);raise RuntimeError('Already filled')
        self.b.cancel=cancel
        from datetime import timedelta
        self.e.manage_orders(self.s,h.NOW+timedelta(seconds=61))
        self.assertFalse(self.s['paused']);self.assertEqual(self.s['positions']['US.A']['qty'],1)
    def test_cancel_race_with_replaced_owned_state_uses_fresh_order(self):
        from copy import deepcopy
        from datetime import timedelta
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW)
        native=self.e.reconcile
        def refresh(state,now):
            updated=deepcopy(state)
            native(updated,now)
            state.clear();state.update(updated)
            return state
        self.e.reconcile=refresh
        def cancel(oid):
            self.b.fill(1,10)
            raise RuntimeError('Already filled')
        self.b.cancel=cancel
        self.e.manage_orders(self.s,h.NOW+timedelta(seconds=61))
        self.assertFalse(self.s['paused'])
        self.assertEqual(self.s['positions']['US.A']['qty'],1)
        self.assertFalse(any('Cancel not confirmed' in str(a) for a in self.s['alerts']))

    def test_unrelated_external_position_not_retried_away(self):
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW);self.b.pos['US.EXTERNAL']=1
        self.e.reconcile(self.s,h.NOW);self.assertTrue(self.s['paused'])

    def test_required_cancel_does_not_wait_for_failing_news(self):
        from datetime import timedelta
        from unittest.mock import patch
        self.e.submit(self.s,'US.A','BUY',1,10,h.NOW)
        with patch.object(self.e,'news_check',side_effect=RuntimeError('news unavailable')) as news:
            self.e.manage_orders(self.s,h.NOW+timedelta(seconds=61))
            news.assert_not_called()
        self.assertEqual(next(iter(self.s['orders'].values()))['status'],'CANCELLED_ALL')
