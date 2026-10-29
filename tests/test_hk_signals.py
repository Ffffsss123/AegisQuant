import unittest
from datetime import datetime,timezone
from trading.hk_signals import current_signals
from trading.policy import SafetyError
class HKSignals(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,18,2,tzinfo=timezone.utc)
        self.cal=[dict(time=d,trade_date_type='WHOLE') for d in ('2026-09-16','2026-09-17','2026-09-18')]
        self.scan=dict(date='2026-09-17',published_at='2026-09-17T09:00:00+00:00',complete=True,errors={},orders_enabled=False,signals={},candidates=[])
    def test_no_candidates_valid_cash_result(self):
        self.assertEqual(current_signals(self.scan,self.cal,self.now)['candidates'],[])
    def test_old_scan_blocked_even_if_complete(self):
        self.scan['date']='2026-09-16'
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now)
    def test_future_or_partial_scan_blocked(self):
        self.scan['published_at']='2026-09-19T00:00:00+00:00'
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now)
        self.scan['published_at']='2026-09-17T09:00:00+00:00';self.scan['complete']=False
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now)
    def test_candidate_without_matching_signal_blocked(self):
        self.scan['candidates']=[dict(code='HK.00001',buy=True,liquid=True)]
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now)
    def test_lunch_blocked(self):
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now.replace(hour=4,minute=30))
    def test_publication_before_completed_close_blocked(self):
        self.scan['published_at']='2026-09-17T08:00:00+00:00'
        with self.assertRaises(SafetyError):current_signals(self.scan,self.cal,self.now)
