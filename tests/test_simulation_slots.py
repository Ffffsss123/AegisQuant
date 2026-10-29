import unittest
from trading.simulation_slots import occupied_slots, require_capacity
from trading.policy import SafetyError

class SimulationSlots(unittest.TestCase):
    def row(self, code, category='US_STOCK', state='HELD', owner='stock'):
        return dict(code=code, category=category, state=state, owner=owner, account='sim')
    def test_us_two_does_not_consume_hk_slots(self):
        rows=[self.row('US.A'),self.row('US.B')]
        self.assertEqual(require_capacity(rows,'HK_STOCK',env='SIMULATE')['US_STOCK'],2)
        with self.assertRaises(SafetyError):require_capacity(rows,'US_STOCK',env='SIMULATE')
    def test_unknown_and_pending_entries_occupy_slots(self):
        rows=[self.row('US.A',state='ENTRY_UNKNOWN'),self.row('US.B',state='ENTRY_PENDING')]
        with self.assertRaises(SafetyError):require_capacity(rows,'US_STOCK',env='SIMULATE')
    def test_partial_fill_and_remaining_order_count_once(self):
        self.assertEqual(occupied_slots([self.row('US.A'),self.row('US.A',state='ENTRY_PENDING')],env='SIMULATE')['US_STOCK'],1)
    def test_owner_conflict_rejected(self):
        with self.assertRaises(SafetyError):occupied_slots([self.row('US.A'),self.row('US.A',owner='other')],env='SIMULATE')
    def test_real_and_unverified_rejected(self):
        with self.assertRaises(SafetyError):occupied_slots([],env='REAL')
        with self.assertRaises(SafetyError):occupied_slots([self.row('US.A',state='UNKNOWN')],env='SIMULATE')
    def test_options_cross_market_share_category(self):
        rows=[self.row('US.OPT','OPTION',owner='option'),self.row('HK.OPT','OPTION',owner='option')]
        with self.assertRaises(SafetyError):require_capacity(rows,'OPTION',env='SIMULATE')

    def test_market_misclassification_rejected(self):
        with self.assertRaises(SafetyError):occupied_slots([self.row('US.A','HK_STOCK')],env='SIMULATE')
    def test_malformed_or_whitespace_identity_rejected(self):
        for row in (None, self.row(' US.A'), self.row('US.A',owner='stock ')):
            with self.assertRaises(SafetyError):occupied_slots([row],env='SIMULATE')

class LedgerMapping(unittest.TestCase):
    def test_partial_fill_and_unknown_buy_are_counted_once(self):
        from trading.simulation_slots import ledger_slots
        s=dict(env='SIMULATE',account='8652',positions={'US.A':dict(qty=1,kind='STOCK')},orders={
            'partial':dict(code='US.A',kind='STOCK',side='BUY',status='FILLED_PART'),
            'unknown':dict(code='US.B',kind='STOCK',side='BUY',status='UNKNOWN'),
            'exit':dict(code='US.A',kind='STOCK',side='SELL',status='SUBMITTED')})
        self.assertEqual(occupied_slots(ledger_slots({'stock':s}),env='SIMULATE')['US_STOCK'],2)
        s['env']='REAL'
        with self.assertRaises(SafetyError):ledger_slots({'stock':s})
