import unittest
from datetime import datetime,timedelta,timezone
from trading.moomoo import Moomoo,NY
from trading.policy import SafetyError

class MarkClock(unittest.TestCase):
    def make(self,offset=0):
        b=object.__new__(Moomoo);now=datetime(2026,9,11,15,40,tzinfo=timezone.utc)
        b.now=lambda:now
        b.snapshots=lambda codes:[dict(update_time=(now+timedelta(seconds=offset)).astimezone(NY).replace(tzinfo=None).isoformat(),last_price=91.5)]
        b.completed_session=lambda n:'2026-09-10';b.execution_session=lambda n,strict:('2026-09-11','2026-09-10')
        return b,now
    def test_slow_reconciliation_does_not_make_fresh_price_future(self):
        b,now=self.make();self.assertEqual(b.mark('US.SWKS',now-timedelta(seconds=15)),91.5)
    def test_genuinely_future_source_still_rejected(self):
        b,now=self.make(6)
        with self.assertRaisesRegex(SafetyError,'Future mark'):b.mark('US.SWKS',now-timedelta(seconds=15))
    def test_stale_source_not_replaced_with_receipt_time(self):
        b,now=self.make(-31)
        with self.assertRaisesRegex(SafetyError,'Stale intraday mark'):b.mark('US.SWKS',now-timedelta(seconds=15))
    def test_old_trade_can_use_fresh_validated_bid(self):
        b,now=self.make(-60)
        b.book=lambda code:dict(bid=91.49,ask=91.5,bid_time=now.isoformat(),ask_time=now.isoformat())
        self.assertEqual(b.mark('US.SWKS',now),91.49)
    def test_old_trade_and_stale_book_still_rejected(self):
        b,now=self.make(-60);old=(now-timedelta(seconds=31)).isoformat()
        b.book=lambda code:dict(bid=91.49,ask=91.5,bid_time=old,ask_time=old)
        with self.assertRaisesRegex(SafetyError,'Stale intraday mark'):b.mark('US.SWKS',now)
    def test_failed_bid_fallback_records_specific_cause(self):
        b,now=self.make(-60);records=[];b.audit=lambda k,v:records.append((k,v))
        b.book=lambda code:dict(bid=90,ask=92,bid_time=now.isoformat(),ask_time=now.isoformat())
        with self.assertRaisesRegex(SafetyError,'Stale intraday mark'):b.mark('US.SWKS',now)
        self.assertEqual(records[-1][0],'valuation_unavailable');self.assertEqual(records[-1][1]['reason'],'Spread too wide')
        self.assertEqual(records[-1][1]['code'],'US.SWKS')
    def test_failed_subscription_records_error_without_inventing_book(self):
        b,now=self.make(-60);records=[];b.audit=lambda k,v:records.append(v)
        b.book=lambda code:(_ for _ in ()).throw(SafetyError('Waiting source timestamp'))
        with self.assertRaises(SafetyError):b.mark('US.SWKS',now)
        self.assertIsNone(records[-1]['book']);self.assertEqual(records[-1]['reason'],'Waiting source timestamp')
    def test_stock_simulation_valuation_accepts_fresh_wide_bid(self):
        b,now=self.make(-60);b.env='SIMULATE';b.allow_wide_stock_valuation=True
        b.book=lambda code:dict(bid=90,ask=92,bid_time=now.isoformat(),ask_time=now.isoformat())
        self.assertEqual(b.mark('US.SWKS',now),90)
        b.env='REAL'
        with self.assertRaises(SafetyError):b.mark('US.SWKS',now)
    def test_wide_simulation_valuation_still_rejects_stale_side(self):
        b,now=self.make(-60);b.env='SIMULATE';b.allow_wide_stock_valuation=True
        b.book=lambda code:dict(bid=90,ask=92,bid_time=now.isoformat(),ask_time=(now-timedelta(seconds=31)).isoformat())
        with self.assertRaises(SafetyError):b.mark('US.SWKS',now)
