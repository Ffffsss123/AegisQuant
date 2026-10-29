import tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from trading.rolling_scan import atomic
from trading.hk_scan_schedule import HKScanSchedule
class HKScanScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.market=self.root/'data/trading/hk_market';self.calls=[]
        outer=self
        class C:
            def running(self,key):return False
            def start(self,key):outer.calls.append(key)
        self.c=C();self.now=datetime(2026,9,17,8,15,tzinfo=timezone.utc)
        atomic(self.market/'catalog.json',dict(source_date='2026-09-17'))
        atomic(self.market/'scan_calendar.json',dict(range_end='2026-10-01',rows=[dict(time='2026-09-16',trade_date_type='WHOLE'),dict(time='2026-09-17',trade_date_type='WHOLE')]))
    def tick(self,seconds=0):return HKScanSchedule(self.c,self.root).tick(self.now+timedelta(seconds=seconds))
    def test_complete_result_no_duplicate(self):
        atomic(self.market/'scan.json',dict(date='2026-09-17',complete=True,errors={}))
        self.assertEqual(self.tick()['status'],'complete');self.assertEqual(self.calls,[])
    def test_restart_preserves_retry(self):
        self.tick();self.assertEqual(self.tick(1)['status'],'retry_wait');self.tick(10);self.assertEqual(len(self.calls),1)
        self.tick(302);self.assertEqual(len(self.calls),2)
    def test_incomplete_does_not_count_as_pass(self):
        atomic(self.market/'scan.json',dict(date='2026-09-17',complete=False,errors={'x':'missing'}))
        self.tick();self.assertEqual(self.calls,['hk_scan'])
    def test_directory_must_be_current(self):
        atomic(self.market/'catalog.json',dict(source_date='2026-09-16'))
        self.tick();self.assertEqual(self.calls,[])
    def test_bad_calendar_isolated_from_other_schedulers(self):
        atomic(self.market/'scan_calendar.json',dict(range_end='2026-10-01',rows=[dict(time='2026-09-17',trade_date_type='UNKNOWN')]))
        self.assertEqual(self.tick()['status'],'needs_attention');self.assertEqual(self.calls,[])
