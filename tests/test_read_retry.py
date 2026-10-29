from pathlib import Path
from unittest.mock import patch as reset_test_patch
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from trading.moomoo import Moomoo
from trading.policy import SafetyError
from trading.read_retry import ReadQueryTimeout
from trading.paper_probe import ProbeStep
from trading.desktop_worker import resume_empty_probe
import test_quote_feed as qtf
from test_trading import NOW

class AdapterTimeout(unittest.TestCase):
    def broker(self):
        b=Moomoo.__new__(Moomoo);b._last={};b.m=SimpleNamespace(RET_OK=0);return b
    def test_read_response_is_typed_without_inline_retries(self):
        b=self.broker();fn=Mock(return_value=(-1,'NN_ProtoRet_TimeOut'))
        with self.assertRaises(ReadQueryTimeout):b.call('positions',fn)
        self.assertEqual(fn.call_count,1)
    def test_mutations_never_get_read_retry_type(self):
        for name in ('place','cancel'):
            fn=Mock(return_value=(-1,'NN_ProtoRet_TimeOut'))
            with self.assertRaises(SafetyError) as ctx:self.broker().call(name,fn)
            self.assertNotIsInstance(ctx.exception,ReadQueryTimeout);fn.assert_called_once()
    def test_unknown_error_not_retried(self):
        with self.assertRaises(SafetyError) as ctx:self.broker().call('positions',lambda:(-1,'Permission denied'))
        self.assertNotIsInstance(ctx.exception,ReadQueryTimeout)
    def test_python_timeout_only_typed_for_reads(self):
        fn=Mock(side_effect=TimeoutError('socket timed out'))
        with self.assertRaises(ReadQueryTimeout):self.broker().call('orders',fn)
        with self.assertRaises(TimeoutError):self.broker().call('place',fn)

class ProbeReadRetry(unittest.TestCase):
    setUp=qtf.ProbeFlow.setUp
    tearDown=qtf.ProbeFlow.tearDown
    def test_initial_read_timeout_then_recovery(self):
        with patch.object(self.e,'manage_orders',side_effect=ReadQueryTimeout('positions')):
            self.assertEqual(self.step.tick(self.s,NOW),'query_retry')
        self.assertFalse(self.s['paused']);self.assertEqual(self.s['orders'],{})
        with patch.object(self.e,'manage_orders') as manage:
            self.assertEqual(self.step.tick(self.s,NOW+timedelta(seconds=4)),'query_retry');manage.assert_not_called()
        self.step.tick(self.s,NOW+timedelta(seconds=5))
        self.assertNotIn('query_retry',self.s);self.assertEqual(len(self.s['orders']),1)
    def test_retry_budget_survives_restart_and_exhausts(self):
        now=NOW
        with patch.object(self.e,'manage_orders',side_effect=ReadQueryTimeout('positions')):
            for delay in (5,15,30):
                self.assertEqual(self.step.tick(self.s,now),'query_retry')
                now+=timedelta(seconds=delay);self.step=ProbeStep(self.e,'US.AA')
            self.assertEqual(self.step.tick(self.s,now),'needs_attention')
        self.assertTrue(self.s['paused']);self.assertTrue(self.s['query_retry']['exhausted']);self.assertEqual(self.s['orders'],{})
    def test_timeout_after_phase_claim_rolls_back_without_intent(self):
        with patch.object(self.e,'submit',side_effect=ReadQueryTimeout('positions')):
            self.assertEqual(self.step.tick(self.s,NOW),'query_retry')
        self.assertEqual(self.s['probe_phase'],'cancel_test');self.assertEqual(self.s['orders'],{})
    @reset_test_patch('trading.desktop_worker.ROOT', Path('/nonexistent/test-only-controls'))
    def test_legacy_empty_timeout_can_resume_after_remote_checks(self):
        self.s.update(paused=True,alerts=['SafetyError: NN_ProtoRet_TimeOut']);self.db.save(self.s)
        self.assertEqual(resume_empty_probe(self.db,self.b),'chosen');self.assertFalse(self.db.load()['paused'])
    def test_legacy_timeout_cannot_resume_later_phase(self):
        self.s.update(paused=True,probe_phase='buy_wait',alerts=['SafetyError: NN_ProtoRet_TimeOut']);self.db.save(self.s)
        with self.assertRaises(SafetyError):resume_empty_probe(self.db,self.b)
    def test_lost_submission_not_retried(self):
        self.b.fail=True;self.step.tick(self.s,NOW)
        self.assertTrue(self.s['paused']);self.assertEqual(self.b.calls,1)
        self.assertEqual(self.step.tick(self.s,NOW+timedelta(seconds=5)),'needs_attention');self.assertEqual(self.b.calls,1)

    def test_recovery_reconciles_before_advancing(self):
        self.s['query_retry']=dict(failures=1,next_at=NOW.isoformat(),exhausted=False)
        calls=[]
        with patch.object(self.e,'reconcile',side_effect=lambda *a:calls.append('reconcile')),patch.object(self.step,'advance_tick',side_effect=lambda *a:calls.append('advance') or 'running'):
            self.step.tick(self.s,NOW)
        self.assertEqual(calls,['reconcile','advance'])
    def test_remote_activity_blocks_empty_timeout_resume(self):
        self.s.update(paused=True,alerts=['SafetyError: NN_ProtoRet_TimeOut']);self.db.save(self.s)
        self.b.remote=[dict(id='external',status='CANCELLED_ALL',code='US.AA',side='BUY',qty=1,filled=0,average=0)]
        with self.assertRaises(SafetyError):resume_empty_probe(self.db,self.b)
        self.assertTrue(self.db.load()['paused'])

if __name__=='__main__':unittest.main()
