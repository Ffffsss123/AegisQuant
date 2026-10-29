import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from datetime import timedelta
from trading.rolling_scan import RollingScan,DailyBroker,evaluate_bars,snapshot_exclusion,atomic
from trading.scanner import scan
from trading.policy import SafetyError
from test_trading import NOW,bars

class Rolling(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.bars=bars();self.day=self.bars[-1]['date'];self.calendar=[b['date'] for b in self.bars]
        self.b=Mock();self.b.completed_session.return_value=self.day
        self.b.calendar.return_value=[dict(time=d) for d in self.calendar]
        self.b.subscription_room.return_value=100;self.b.daily.return_value=self.bars
        self.codes=['US.A','US.B','US.C']
        basic={c:dict(code=c,delisting=False,lot_size=1,suspension=False) for c in self.codes}
        directory=[dict(Symbol=c[3:],**{'Security Name':'Example Common Stock','Test Issue':'N','ETF':'N','_exchange':'NASDAQ'}) for c in self.codes]
        atomic(self.path/('catalog_'+self.day+'.json'),dict(basic=basic,directory=directory))
        self.b.snapshots.side_effect=lambda codes:[dict(code=c,suspension=False,last_price=.5 if c=='US.C' else 10,update_time=self.day+' 16:00:00') for c in codes]
    def tearDown(self):self.tmp.cleanup()
    def scanner(self):return RollingScan(self.b,self.path,self.path/'progress.json',NOW)
    def run_scan(self,s):
        with patch('trading.rolling_scan.time.monotonic',side_effect=range(0,100000,100)),patch('builtins.print'):
            return s.run()
    def test_complete_coverage_separates_exclusions_from_histories(self):
        r=self.run_scan(self.scanner())
        self.assertTrue(r['complete']);self.assertEqual(r['processed_count'],3)
        self.assertEqual(r['history_count'],2);self.assertEqual(r['excluded_count'],1)
        self.b.unsubscribe_daily.assert_called_once_with(['US.A','US.B'])
        self.b.history.assert_not_called()
    def test_resume_uses_completed_session_provenance(self):
        self.run_scan(self.scanner());self.b.daily.reset_mock()
        self.assertTrue(self.run_scan(self.scanner())['complete']);self.b.daily.assert_not_called()
    def test_old_cache_remains_until_replacement(self):
        p=self.path/'history/US.A.json';old=self.bars[:-1];atomic(p,old)
        self.b.daily.side_effect=SafetyError('network interrupted')
        s=self.scanner()
        with self.assertRaises(SafetyError):self.run_scan(s)
        self.assertEqual(json.loads(p.read_text()),old)
        with patch('builtins.print'):r=s.save('needs_attention')
        self.assertFalse(r['complete']);self.assertGreater(r['pending_count'],0)
    def test_gap_is_excluded_with_reason_not_padded(self):
        broken=self.bars+[{**self.bars[-1],'date':'2026-03-02'}]
        self.assertEqual(evaluate_bars(broken,self.calendar,'2026-03-02')[0],'excluded_history_gaps')
        self.assertEqual(evaluate_bars(self.bars[-20:],self.calendar,self.day)[0],'excluded_insufficient_history')
    def test_missing_turnover_rejected(self):
        broken=[dict(x) for x in self.bars];broken[-1].pop('turnover')
        self.assertEqual(evaluate_bars(broken,self.calendar,self.day)[0],'excluded_invalid_history')
    def test_sdk_optional_nan_is_saved_as_missing_not_a_number(self):
        file=self.path/'nan.json';atomic(file,dict(optional=float('nan'),nested=[float('inf')]))
        self.assertEqual(json.loads(file.read_text()),dict(optional=None,nested=[None]))
    def test_new_session_price_not_used_for_prior_day_exclusion(self):
        snap=dict(suspension=False,last_price=1,update_time='2026-09-10 04:00:00')
        self.assertIsNone(snapshot_exclusion(snap,'2026-09-09'))
        self.assertEqual(snapshot_exclusion(snap,'2026-09-10')[0],'excluded_price')
    def test_missing_snapshot_time_and_zero_price_are_not_valid_inputs(self):
        for snap in [dict(suspension=False,last_price=10,update_time=None),dict(suspension=False,last_price=0,update_time='2026-09-09 16:00:00')]:
            self.assertEqual(snapshot_exclusion(snap,'2026-09-09')[0],'excluded_unverified')
    def test_no_subscription_room_cannot_complete(self):
        self.b.subscription_room.return_value=0;s=self.scanner()
        with self.assertRaises(SafetyError):self.run_scan(s)
        self.b.subscribe_daily.assert_not_called()
    def test_current_daily_removes_unfinished_day_and_preserves_turnover(self):
        import pandas as pd
        import threading
        b=DailyBroker.__new__(DailyBroker);b._last={};b._daily_lock=threading.Lock()
        b.m=SimpleNamespace(RET_OK=0,SubType=SimpleNamespace(K_DAY='K_DAY'),AuType=SimpleNamespace(QFQ='QFQ'))
        rows=[dict(code='US.A',time_key=d+' 00:00:00',open=10,high=11,low=9,close=10,volume=100,turnover=1001) for d in ['2026-09-09','2026-09-10']]
        b.q=SimpleNamespace(get_cur_kline=Mock(return_value=(0,pd.DataFrame(rows))))
        result=b.daily('US.A','2026-09-09');self.assertEqual(len(result),1);self.assertEqual(result[0]['turnover'],1001)
        b.q.get_cur_kline.side_effect=[(0,pd.DataFrame()),(0,pd.DataFrame(rows))]
        with patch('trading.rolling_scan.time.sleep'):
            result=b.daily('US.A','2026-09-09')
        self.assertEqual(len(result),1)
    def test_empty_initial_cache_is_retried_after_restart(self):
        file=self.path/'history/US.A.json';atomic(file,[])
        atomic(file.with_suffix('.meta.json'),dict(session=self.day,source='moomoo_subscribed_daily'))
        self.run_scan(self.scanner())
        self.assertIn(('US.A',self.day),[c.args for c in self.b.daily.call_args_list])
    def test_replaced_previous_session_history_is_archived(self):
        old=self.bars[:-1];atomic(self.path/'history/US.A.json',old)
        self.run_scan(self.scanner())
        archived=self.path/'history_archive'/old[-1]['date']/'US.A.json'
        self.assertEqual(json.loads(archived.read_text()),old)
    def test_cached_complete_result_not_overwritten_by_legacy_scanner(self):
        r=self.run_scan(self.scanner());self.b.basic.reset_mock()
        self.assertEqual(scan(self.b,self.path,NOW),r);self.b.basic.assert_not_called()
    def test_incomplete_result_cannot_be_reused_as_complete(self):
        r=self.run_scan(self.scanner());r['complete']=False;atomic(self.path/'scan.json',r)
        self.b.history.side_effect=SafetyError('quota')
        with patch('builtins.print'):result=scan(self.b,self.path,NOW,max_requests=0)
        self.assertTrue(result['complete']);self.b.history.assert_not_called()
        self.assertEqual(result['processed_count'],3)

    def test_partial_progress_does_not_replace_published_scan(self):
        old=self.run_scan(self.scanner());s=self.scanner()
        with patch('builtins.print'):s.save('snapshot_inventory')
        self.assertEqual(json.loads((self.path/'scan.json').read_text()),old)
        self.assertFalse(json.loads((self.path/'scan_progress.json').read_text())['complete'])
    def test_held_low_price_gets_exit_signal(self):
        s=RollingScan(self.b,self.path,self.path/'progress.json',NOW,['US.C'])
        r=self.run_scan(s)
        self.assertIn('US.C',r['signals']);self.assertTrue(r['complete'])
    def test_held_outside_universe_keeps_signal_without_new_entry(self):
        s=RollingScan(self.b,self.path,self.path/'progress.json',NOW,['US.GONE'])
        r=self.run_scan(s)
        self.assertIn('US.GONE',r['signals']);self.assertNotIn('US.GONE',[x['code'] for x in r['candidates']])
    def test_held_missing_bars_prevents_publication(self):
        old=self.run_scan(self.scanner());self.b.daily.side_effect=lambda c,d:[] if c=='US.GONE' else self.bars
        s=RollingScan(self.b,self.path,self.path/'progress.json',NOW,['US.GONE']);r=self.run_scan(s)
        self.assertFalse(r['complete']);self.assertIn('US.GONE',r['errors'])
        self.assertEqual(json.loads((self.path/'scan.json').read_text()),old)

    def test_unsubscribe_failure_cannot_publish_complete_result(self):
        self.b.unsubscribe_daily.side_effect=SafetyError('Disconnect during release')
        s=self.scanner()
        with self.assertRaises(SafetyError):self.run_scan(s)
        self.assertFalse((self.path/'scan.json').exists())
        self.assertFalse(json.loads((self.path/'scan_progress.json').read_text())['complete'])

if __name__=='__main__':unittest.main()
