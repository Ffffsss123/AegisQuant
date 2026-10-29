import unittest
from datetime import datetime,timezone
from trading.hk_scan import completed_session
from trading.policy import SafetyError
class HKScanSession(unittest.TestCase):
    def test_full_day_waits_after_auction(self):
        rows=[dict(time='2026-09-16',trade_date_type='WHOLE'),dict(time='2026-09-17',trade_date_type='WHOLE')]
        self.assertEqual(completed_session(rows,datetime(2026,9,17,8,14,tzinfo=timezone.utc)),'2026-09-16')
        self.assertEqual(completed_session(rows,datetime(2026,9,17,8,15,tzinfo=timezone.utc)),'2026-09-17')
    def test_half_day(self):
        rows=[dict(time='2026-09-17',trade_date_type='MORNING')]
        self.assertEqual(completed_session(rows,datetime(2026,9,17,4,15,tzinfo=timezone.utc)),'2026-09-17')
    def test_unknown_calendar_type_blocks(self):
        with self.assertRaises(SafetyError):completed_session([dict(time='2026-09-17',trade_date_type='UNKNOWN')],datetime.now(timezone.utc))
    def test_duplicate_calendar_blocks(self):
        row=dict(time='2026-09-17',trade_date_type='WHOLE')
        with self.assertRaises(SafetyError):completed_session([row,row],datetime.now(timezone.utc))
