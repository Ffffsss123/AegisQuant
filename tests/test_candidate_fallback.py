import unittest
from unittest.mock import patch
from tests import test_swing as sw
from tests import test_trading as t
from trading.candidate_fallback import ranked,enable_for_session
from trading.policy import SafetyError
from trading.news import assess,NewsGuard

class Fallback(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    def start(self,n=12):
        self.s.update(candidate_fallback_enabled=True,purpose='strategy_simulation');self.db.save(self.s)
        self.scan['candidates']=[dict(code='US.'+str(i)) for i in range(n)]
        self.scan['signals']={c['code']:dict(sell=False,stop_distance=1,target_distance=2) for c in self.scan['candidates']}
        return self.e.tick(self.scan,t.NOW)
    def test_limit_and_rank_order(self):self.assertEqual([i['code'] for i in self.start()['queue']],['US.'+str(i) for i in range(10)])
    def test_wide_spread_then_next_candidate(self):
        self.start();good=self.b.book;self.b.book=lambda c:t.book(9,10) if c=='US.0' else good(c)
        self.e.drain(self.scan,t.NOW);self.assertEqual(self.b.calls,0)
        self.e.tick(self.scan,t.NOW);self.e.drain(self.scan,t.NOW)
        self.assertEqual(self.b.remote[0]['code'],'US.1');self.assertIn('2026-09-08:US.0',self.db.load()['candidate_checks'])
    def test_restart_does_not_repeat_skip(self):
        self.start();self.b.book=lambda c:t.book(9,10);self.e.drain(self.scan,t.NOW)
        from trading.swing_engine import SwingEngine
        e=SwingEngine(self.b,self.db,self.e.c,news=lambda *a:dict(multiplier=1));e.tick(self.scan,t.NOW)
        self.assertEqual(self.db.load()['queue'][0]['code'],'US.1')
    def test_global_news_blocks_without_consuming_queue(self):
        self.start();self.e.news=lambda *a:dict(multiplier=0,global_block=True)
        self.e.drain(self.scan,t.NOW);s=self.db.load();self.assertEqual(len(s['queue']),10);self.assertTrue(s['entry_global_block']);self.assertEqual(self.b.calls,0)
    def test_individual_news_can_skip(self):
        self.start();self.e.news=lambda c,n:dict(multiplier=0 if c=='US.0' else 1,global_block=False)
        self.e.drain(self.scan,t.NOW);self.assertEqual(self.db.load()['queue'][0]['code'],'US.1')
    def test_unknown_error_stops_alternatives(self):
        self.start();self.b.book=lambda c:(_ for _ in ()).throw(SafetyError('connection fault'))
        self.e.drain(self.scan,t.NOW);self.assertTrue(self.db.load()['paused']);self.e.drain(self.scan,t.NOW);self.assertEqual(self.b.calls,0)
    def test_unknown_order_not_replaced(self):
        self.start();self.b.fail=True;self.e.drain(self.scan,t.NOW);self.e.tick(self.scan,t.NOW);self.e.drain(self.scan,t.NOW)
        self.assertEqual(self.b.calls,1);self.assertTrue(self.db.load()['paused'])
    def test_two_positions_stop_remaining_queue(self):
        self.start()
        for i in range(2):
            self.e.drain(self.scan,t.NOW);self.b.fill(self.b.remote[-1]['qty'],10.01);self.e.tick(self.scan,t.NOW)
        self.e.drain(self.scan,t.NOW);self.assertEqual(self.b.calls,2);self.assertFalse(self.db.load()['queue'])
    def test_all_rejected_stays_cash(self):
        self.start();self.b.book=lambda c:t.book(9,10)
        for _ in range(12):self.e.tick(self.scan,t.NOW);self.e.drain(self.scan,t.NOW)
        s=self.db.load();self.assertEqual(s['cash'],600);self.assertEqual(len(s['candidate_checks']),10);self.assertFalse(s['queue']);self.assertEqual(self.b.calls,0)
    def test_paused_no_alternatives(self):
        self.start();s=self.db.load();s['paused']=True;self.db.save(s);self.e.drain(self.scan,t.NOW);self.assertEqual(self.b.calls,0)
    def test_duplicate_codes_removed(self):
        scan=dict(candidates=[dict(code='US.A')]*3+[dict(code='US.B')]);self.assertEqual(len(ranked(scan,self.s,'2026-09-08',10)),2)
    def test_account_mismatch_blocks(self):
        self.start();self.b.pos={'US.EXTERNAL':1};self.e.drain(self.scan,t.NOW);self.assertTrue(self.db.load()['paused']);self.assertEqual(self.b.calls,0)
    def test_pending_cancel_not_blocked_by_news(self):
        self.start();self.e.drain(self.scan,t.NOW);self.e.news=lambda *a:dict(multiplier=0,global_block=True)
        self.e.drain(self.scan,t.NOW);self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL')

    def test_normal_entry_does_not_duplicate_guard_reconciliation(self):
        self.start()
        with patch.object(self.e,'manage_orders',wraps=self.e.manage_orders) as manage:
            self.e.drain(self.scan,t.NOW)
            self.assertEqual(manage.call_count,1)
            self.assertTrue(manage.call_args.kwargs['reconciled'])

class NewsScope(unittest.TestCase):
    def snapshot(self):return dict(version=3,required_sources=['feed'],health={k:dict(ok=True,checked_at=t.NOW.isoformat()) for k in ('feed','US.A')},events=[])
    def test_feed_failure_is_global(self):
        s=self.snapshot();s['health']['feed']['ok']=False;self.assertTrue(assess(s,'US.A',t.NOW)['global_block'])
    def test_individual_coverage_is_local(self):
        s=self.snapshot();s['health']['US.A']['ok']=False;r=assess(s,'US.A',t.NOW);self.assertFalse(r['global_block']);self.assertEqual(r['multiplier'],0)
    def test_corrupt_cache_is_global(self):self.assertTrue(NewsGuard('/nonexistent')('US.A',t.NOW)['global_block'])

class Migration(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    def prepare(self):
        from datetime import timedelta
        self.s.update(purpose='strategy_simulation',processed=['2026-09-08'],queue_day='2026-09-08',queue=[],temporary_entry_window=dict(session='2026-09-08',starts_at=t.NOW.isoformat(),expires_at=(t.NOW+timedelta(hours=2)).isoformat()))
        self.db.save(self.s)
        self.scan['candidates']=[dict(code='US.'+str(i)) for i in range(12)]
        self.db.event('skipped',dict(code='US.0',reason='Spread too wide'))
        with self.db.db:self.db.db.execute("UPDATE events SET ts=? WHERE kind='skipped'",(t.NOW.isoformat(),))
    def test_preserve_skips_claims_and_idempotent(self):
        self.prepare();s=enable_for_session(self.db,self.scan,'2026-09-08','2026-09-04',t.NOW)
        self.assertEqual(len(s['queue']),9);self.assertEqual(s['queue'][0]['code'],'US.1');self.assertEqual(s['processed'],['2026-09-08'])
        self.assertEqual(enable_for_session(self.db,self.scan,'2026-09-08','2026-09-04',t.NOW),s)
    def test_stale_scan_rejected(self):
        self.prepare()
        with self.assertRaises(SafetyError):enable_for_session(self.db,self.scan,'2026-09-08','2026-09-03',t.NOW)
    def test_paused_never_cleared(self):
        self.prepare();s=self.db.load();s['paused']=True;self.db.save(s)
        with self.assertRaises(SafetyError):enable_for_session(self.db,self.scan,'2026-09-08','2026-09-04',t.NOW)
        self.assertTrue(self.db.load()['paused'])
    def test_unknown_previous_failure_blocks(self):
        self.prepare();self.db.event('skipped',dict(code='US.X',reason='Network failure'))
        with self.db.db:self.db.db.execute("UPDATE events SET ts=? WHERE kind='skipped'",(t.NOW.isoformat(),))
        with self.assertRaises(SafetyError):enable_for_session(self.db,self.scan,'2026-09-08','2026-09-04',t.NOW)
