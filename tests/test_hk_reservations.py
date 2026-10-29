import tempfile,unittest
from pathlib import Path
from trading.hk_reservations import Reservations
from trading.policy import SafetyError

class DurableReservations(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'holds.db';self.r=Reservations(self.path,'fixture')
    def tearDown(self):self.r.close();self.tmp.cleanup()
    def test_restart_idempotency_and_shared_cap(self):
        self.r.reserve('a',300,500);self.r.close();self.r=Reservations(self.path,'fixture')
        self.assertEqual(self.r.reserve('a',300,500)['state'],'HELD')
        with self.assertRaises(SafetyError):self.r.reserve('b',201,500)
        self.assertFalse(self.r.reserve('b',200,500)['orders_enabled'])
    def test_second_connection_cannot_double_spend(self):
        other=Reservations(self.path,'fixture')
        try:
            self.r.reserve('a',300,400)
            with self.assertRaises(SafetyError):other.reserve('b',200,400)
        finally:other.close()
    def test_unknown_partial_cancel_failures_retain_hold(self):
        self.r.reserve('a',500,500)
        for status,qty in [('UNKNOWN',0),('SUBMITTED',0),('CANCELLED_PART',1),('FILLED_ALL',1)]:
            with self.assertRaises(SafetyError):self.r.release_unfilled('a',broker_status=status,filled_qty=qty,reconciled=True,evidence='fixture')
        with self.assertRaises(SafetyError):self.r.reserve('b',1,500)
    def test_confirmed_unfilled_release_preserves_tombstone(self):
        self.r.reserve('a',500,500)
        self.r.release_unfilled('a',broker_status='CANCELLED_ALL',filled_qty=0,reconciled=True,evidence='fixture')
        self.r.reserve('b',500,500)
        with self.assertRaises(SafetyError):self.r.reserve('a',500,500)
    def test_identity_and_budget_validation(self):
        with self.assertRaises(SafetyError):Reservations(self.path,'other')
        for v,c in [(0,500),(-1,500),(1,890),('NaN',500)]:
            with self.assertRaises(SafetyError):self.r.reserve('x',v,c)
    def test_invalid_release_evidence_preserves_full_hold_after_restart(self):
        self.r.reserve('a',500,500)
        for evidence in ('', '   ', '\n', 1, True, {'status':'cancelled'}):
            with self.assertRaises(SafetyError):
                self.r.release_unfilled('a',broker_status='CANCELLED_ALL',filled_qty=0,
                                        reconciled=True,evidence=evidence)
        self.r.close();self.r=Reservations(self.path,'fixture')
        self.assertEqual(self.r.committed_and_held(),500)
        with self.assertRaises(SafetyError):self.r.reserve('b',1,500)
    def settle(self,**changes):
        args=dict(debit_usd=120,filled_qty=100,broker_status='CANCELLED_PART',reconciled=True,evidence='broker fixture')
        args.update(changes);return self.r.settle_fill('a',**args)
    def test_terminal_partial_fill_replaces_hold_across_restart(self):
        self.r.reserve('a',300,500);self.settle()
        self.r.close();self.r=Reservations(self.path,'fixture')
        self.assertEqual(self.r.committed_and_held(),120)
        self.assertEqual(self.settle()['state'],'COMMITTED')
        self.r.reserve('b',380,500)
        with self.assertRaises(SafetyError):self.r.reserve('c',1,500)
    def test_nonterminal_and_conflicting_evidence_cannot_free_capital(self):
        self.r.reserve('a',300,500)
        for change in ({'broker_status':'FILLED_PART'},{'reconciled':False},{'filled_qty':0},{'filled_qty':0.5},{'debit_usd':'NaN'},{'evidence':''}):
            with self.assertRaises(SafetyError):self.settle(**change)
            self.assertEqual(self.r.committed_and_held(),300)
        self.settle()
        with self.assertRaises(SafetyError):self.settle(debit_usd=119)
        with self.assertRaises(SafetyError):self.r.release_unfilled('a',broker_status='CANCELLED_ALL',filled_qty=0,reconciled=True,evidence='conflicting')
        self.assertEqual(self.r.committed_and_held(),120)
    def test_cost_overrun_is_retained_and_blocks_new_capital(self):
        self.r.reserve('a',500,500);self.settle(debit_usd=503,broker_status='FILLED_ALL')
        self.assertEqual(self.r.committed_and_held(),503)
        with self.assertRaises(SafetyError):self.r.reserve('b',1,500)
    def test_released_or_missing_hold_cannot_become_position(self):
        with self.assertRaises(SafetyError):self.settle()
        self.r.reserve('a',300,500)
        self.r.release_unfilled('a',broker_status='CANCELLED_ALL',filled_qty=0,reconciled=True,evidence='fixture')
        with self.assertRaises(SafetyError):self.settle()

    def test_original_request_replay_after_partial_fill_and_restart(self):
        self.r.reserve('a',300,500);self.settle()
        self.r.close();self.r=Reservations(self.path,'fixture')
        replay=self.r.reserve('a',300,0)
        self.assertEqual(replay['state'],'COMMITTED')
        self.assertEqual(float(replay['amount_usd']),120)
        with self.assertRaises(SafetyError):self.r.reserve('a',120,500)
        self.assertEqual(self.r.committed_and_held(),120)

    def test_legacy_committed_request_is_not_guessed(self):
        self.r.reserve('a',300,500);self.settle()
        self.r.db.execute('DROP TABLE reservation_intents')
        self.r.close();self.r=Reservations(self.path,'fixture')
        for value in (120,300):
            with self.assertRaises(SafetyError):self.r.reserve('a',value,500)
        self.assertEqual(self.r.committed_and_held(),120)

    def test_legacy_unfilled_request_preserves_original_intent(self):
        self.r.reserve('a',300,500)
        self.r.db.execute('DROP TABLE reservation_intents')
        self.r.close();self.r=Reservations(self.path,'fixture')
        self.settle()
        self.assertEqual(self.r.reserve('a',300,500)['state'],'COMMITTED')

    def close_position(self,**changes):
        args=dict(net_proceeds_usd=110,sold_qty=100,order_ids=['sell-1'],
                  position_flat=True,no_pending_orders=True,reconciled=True,evidence='fixture')
        args.update(changes)
        return self.r.settle_closed_position('a',**args)

    def test_full_close_releases_once_and_preserves_loss_after_restart(self):
        self.r.reserve('a',300,500);self.settle();self.close_position()
        self.r.close();self.r=Reservations(self.path,'fixture')
        self.assertEqual(self.close_position()['realized_pnl_usd'],'-10')
        self.assertEqual(self.r.realized_pnl(),-10)
        self.assertEqual(self.r.committed_and_held(),0)
        with self.assertRaises(SafetyError):self.r.reserve('a',300,500)

    def test_incomplete_close_keeps_all_committed_capital(self):
        self.r.reserve('a',300,500);self.settle()
        for change in [dict(sold_qty=50),dict(position_flat=False),dict(no_pending_orders=False),
                       dict(reconciled=False),dict(evidence=' '),dict(order_ids=[]),
                       dict(order_ids=['s','s']),dict(net_proceeds_usd='NaN')]:
            with self.assertRaises(SafetyError):self.close_position(**change)
            self.assertEqual(self.r.committed_and_held(),120)
            self.assertEqual(self.r.realized_pnl(),0)

    def test_conflicting_close_and_reused_order_do_not_release_other_position(self):
        self.r.reserve('a',300,500);self.settle();self.close_position()
        with self.assertRaises(SafetyError):self.close_position(net_proceeds_usd=111)
        self.r.reserve('b',100,500)
        self.r.settle_fill('b',debit_usd=100,filled_qty=100,broker_status='FILLED_ALL',reconciled=True,evidence='fixture')
        with self.assertRaises(SafetyError):
            self.r.settle_closed_position('b',net_proceeds_usd=110,sold_qty=100,order_ids=['sell-1'],position_flat=True,no_pending_orders=True,reconciled=True,evidence='fixture')
        self.assertEqual(self.r.committed_and_held(),100)
        self.assertEqual(self.r.realized_pnl(),-10)

    def test_unfilled_hold_cannot_be_released_as_closed_position(self):
        self.r.reserve('a',300,500)
        with self.assertRaises(SafetyError):self.close_position()
        self.assertEqual(self.r.committed_and_held(),300)

    def test_risk_totals_count_entry_and_exit_fees_once(self):
        self.r.reserve('a',300,500);self.settle(debit_usd=120)
        r=self.r.risk_totals({'a':dict(market_value_usd=125,exit_fee_reserve_usd=3)})
        self.assertEqual(r['committed_debit_usd'],'120')
        self.assertEqual(r['held_usd'],'0')
        self.assertEqual(r['exit_fee_reserve_usd'],'3')
        self.assertEqual(r['occupied_usd'],'123');self.assertEqual(r['unrealized_net_usd'],'2')
        self.assertEqual(r['realized_net_usd'],'0');self.assertFalse(r['pending_or_unknown'])
        self.close_position(net_proceeds_usd=122)
        r=self.r.risk_totals({})
        self.assertEqual(r['occupied_usd'],'0');self.assertEqual(r['realized_net_usd'],'2')
        self.assertEqual(r['unrealized_net_usd'],'0')

    def test_unmatched_valuation_fails_closed_and_pending_holds_remain(self):
        self.r.reserve('a',300,500)
        self.assertTrue(self.r.risk_totals({})['pending_or_unknown'])
        self.assertEqual(self.r.risk_totals({})['occupied_usd'],'300')
        self.settle()
        for values in ({},{'wrong':dict(market_value_usd=125,exit_fee_reserve_usd=3)},
                       {'a':dict(market_value_usd=125,exit_fee_reserve_usd=0)}):
            with self.assertRaises(SafetyError):self.r.risk_totals(values)
        self.assertEqual(self.r.committed_and_held(),120)
