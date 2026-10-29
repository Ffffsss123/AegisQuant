import unittest
from datetime import timedelta
from tests import test_swing as sw
from tests.test_trading import NOW
from trading.swing_engine import SwingEngine
from trading.policy import Config

class TemporaryWindow(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    def enable(self):
        self.s['temporary_entry_window']=dict(session='2026-09-08',starts_at=NOW.isoformat(),expires_at=(NOW+timedelta(hours=6)).isoformat())
        self.s['entry_after_session']='2026-09-08'
        self.db.save(self.s);now=NOW+timedelta(hours=1);self.b.now_value=now;return now
    def test_current_day_delay_bypassed_only_with_override(self):
        now=self.enable();s=self.e.tick(self.scan,now);self.assertEqual(s['queue'][0]['side'],'BUY')
    def test_outside_default_window_can_submit_in_simulation(self):
        now=self.enable();self.e.tick(self.scan,now);self.e.drain(self.scan,now);self.assertEqual(self.b.calls,1)
    def test_no_override_keeps_original_window(self):
        now=NOW+timedelta(hours=1);self.b.now_value=now;self.assertFalse(self.e.tick(self.scan,now).get('queue'))
    def test_restart_does_not_repeat_daily_queue(self):
        now=self.enable();self.e.tick(self.scan,now);s=self.db.load();s['queue']=[];self.db.save(s)
        e=SwingEngine(self.b,self.db,Config(),news=lambda *args:dict(multiplier=1))
        self.assertFalse(e.tick(self.scan,now).get('queue'))
    def test_expiry_weekend_and_wrong_day(self):
        now=self.enable()
        for later in [NOW+timedelta(hours=6),NOW+timedelta(days=1),NOW+timedelta(days=4)]:
            self.assertFalse(self.e.temporary_entry_active(self.s,later))
        self.assertIsNone(self.e.entry_session(self.s,NOW+timedelta(hours=8)))
    def test_never_applies_to_real_environment(self):
        now=self.enable();self.s['env']='REAL';self.assertFalse(self.e.temporary_entry_active(self.s,now))
    def test_pause_and_incomplete_scan_still_block(self):
        now=self.enable();self.s['paused']=True;self.db.save(self.s)
        self.assertFalse(self.e.tick(self.scan,now).get('queue'))
        self.s['paused']=False;self.s['processed']=[];self.db.save(self.s)
        self.assertFalse(self.e.tick(dict(self.scan,complete=False),now).get('queue'))
    def test_expired_override_drops_waiting_buy(self):
        now=self.enable();self.e.tick(self.scan,now);self.e.drain(self.scan,NOW+timedelta(hours=6))
        self.assertFalse(self.db.load()['queue']);self.assertEqual(self.b.calls,0)
    def test_invalid_or_future_start_fails_closed(self):
        now=self.enable()
        self.s['temporary_entry_window']['starts_at']='bad';self.assertFalse(self.e.temporary_entry_active(self.s,now))
        self.s['temporary_entry_window']['starts_at']=(now+timedelta(hours=1)).isoformat();self.assertFalse(self.e.temporary_entry_active(self.s,now))

class ContinuousWindow(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    def enable(self):
        self.s['entry_timing']='regular_session';self.db.save(self.s)
        now=NOW+timedelta(hours=2);self.b.now_value=now;return now
    def test_late_entry_and_restart_deduplication(self):
        now=self.enable();self.e.tick(self.scan,now);self.e.drain(self.scan,now)
        self.assertEqual(self.b.calls,1)
        e=SwingEngine(self.b,self.db,Config(),news=lambda *a:dict(multiplier=1))
        e.tick(self.scan,now);e.drain(self.scan,now);self.assertEqual(self.b.calls,1)
    def test_incomplete_scan_does_not_consume_day(self):
        now=self.enable();s=self.e.tick(dict(self.scan,complete=False),now)
        self.assertNotIn('2026-09-08',s['processed'])
        self.assertTrue(self.e.tick(self.scan,now)['queue'])
    def test_pause_does_not_consume_day(self):
        now=self.enable();self.s['paused']=True;self.db.save(self.s)
        s=self.e.tick(self.scan,now);self.assertFalse(s['queue']);self.assertNotIn('2026-09-08',s['processed'])
    def test_real_closed_market_and_handoff_still_restricted(self):
        now=self.enable();self.s['env']='REAL';self.assertIsNone(self.e.entry_session(self.s,now))
        self.s['env']='SIMULATE';self.assertIsNone(self.e.entry_session(self.s,NOW+timedelta(hours=8)))
        self.s['entry_after_session']='2026-09-08';self.db.save(self.s)
        self.assertFalse(self.e.tick(self.scan,now)['queue'])

class ExitSpreadWait(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    buy=sw.Execution.buy
    def prepare(self):
        self.buy();self.scan['signals']['US.A']['sell']=True
        self.e.tick(self.scan,NOW)
    def test_second_quote_recovers_after_restart_without_duplicate(self):
        self.prepare();original=self.b.book;calls=[]
        def book(code):
            calls.append(code)
            return dict(sw.helper.book(9,10),bid_time=(NOW-timedelta(seconds=31)).isoformat()) if len(calls)==2 else original(code)
        self.b.book=book
        self.e.drain(self.scan,NOW)
        s=self.db.load();self.assertEqual(len(s['orders']),1);self.assertTrue(s['queue']);self.assertIn('quote_wait',s)
        e=SwingEngine(self.b,self.db,Config(),news=lambda *a:dict(multiplier=1))
        later=NOW+timedelta(seconds=5);self.b.now_value=later
        e.drain(self.scan,later);self.assertEqual(self.b.calls,2)
        e.drain(self.scan,later);self.assertEqual(self.b.calls,2)
    def test_timeout_survives_restart_and_never_submits(self):
        self.prepare();self.b.book=lambda c:dict(sw.helper.book(9,10),bid_time=(NOW-timedelta(seconds=31)).isoformat())
        self.e.drain(self.scan,NOW)
        e=SwingEngine(self.b,self.db,Config(),news=lambda *a:dict(multiplier=1))
        e.drain(self.scan,NOW+timedelta(seconds=121))
        s=self.db.load();self.assertFalse(s['queue']);self.assertTrue(s['paused']);self.assertEqual(self.b.calls,1)
    def test_wait_does_not_block_pending_order_cancellation(self):
        self.prepare();original=self.b.book;self.b.book=lambda c:dict(sw.helper.book(9,10),bid_time=(NOW-timedelta(seconds=31)).isoformat())
        self.e.drain(self.scan,NOW);self.b.book=original
        s=self.db.load();self.e.submit(s,'US.A','SELL',1,10,NOW)
        later=NOW+timedelta(seconds=61);self.b.now_value=later
        self.b.book=lambda c:dict(bid=9,ask=10,bid_time=(later-timedelta(seconds=31)).isoformat(),ask_time=later.isoformat())
        self.e.drain(self.scan,later)
        self.assertEqual(self.b.remote[-1]['status'],'CANCELLED_ALL');self.assertEqual(self.b.calls,2)
    def test_unknown_submission_is_not_requeued(self):
        self.prepare();self.b.fail=True;self.e.drain(self.scan,NOW)
        s=self.db.load();self.assertFalse(s['queue']);self.assertEqual(self.b.calls,2)
        self.assertTrue(any(o['status']=='UNKNOWN' for o in s['orders'].values()))
        self.e.drain(self.scan,NOW+timedelta(seconds=5));self.assertEqual(self.b.calls,2)
    def test_spread_wait_is_only_simulated_stock_exit(self):
        exc=sw.SafetyError('Spread too wide')
        for env,kind,side in [('REAL','STOCK','SELL'),('SIMULATE','OPTION','SELL'),('SIMULATE','STOCK','BUY')]:
            self.assertFalse(self.e.quote_waitable(dict(env=env),dict(kind=kind,side=side),exc))
