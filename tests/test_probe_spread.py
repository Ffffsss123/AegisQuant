from pathlib import Path
from unittest.mock import patch as reset_test_patch
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_quote_feed as qtf
from test_trading import NOW,book
from trading.paper_probe import ProbeStep
from trading.quote_feed import QuoteUnavailable
from trading.desktop_worker import resume_empty_probe
from trading.policy import SafetyError

class SpreadWait(unittest.TestCase):
    setUp=qtf.ProbeFlow.setUp
    tearDown=qtf.ProbeFlow.tearDown
    def test_wide_then_valid_no_early_order(self):
        self.b.book=lambda code:book(48.04,48.22)
        self.assertEqual(self.step.tick(self.s,NOW),'waiting_spread')
        self.assertEqual(self.b.calls,0);self.assertEqual(self.s['probe_phase'],'cancel_test')
        self.b.book=lambda code:book(48.04,48.10)
        self.step.tick(self.s,NOW+timedelta(seconds=5))
        self.assertEqual(self.b.calls,1);self.assertEqual(self.s['probe_phase'],'cancel_wait')
    def test_second_price_check_wide_rolls_back_before_intent(self):
        with patch.object(self.e,'submit',side_effect=SafetyError('Spread too wide')):
            self.assertEqual(self.step.tick(self.s,NOW),'waiting_spread')
        self.assertEqual(self.s['orders'],{});self.assertEqual(self.s['probe_phase'],'cancel_test')
    def test_wide_timeout_survives_restart(self):
        self.b.book=lambda code:book(48.04,48.22)
        self.step.tick(self.s,NOW);self.step=ProbeStep(self.e,'US.AA')
        with self.assertRaises(QuoteUnavailable) as ctx:self.step.tick(self.s,NOW+timedelta(seconds=121))
        self.assertEqual(ctx.exception.status,'quote_wait_timeout');self.assertEqual(self.b.calls,0)
    def test_wait_services_cancellation(self):
        self.step.tick(self.s,NOW);self.step.tick(self.s,NOW+timedelta(seconds=61))
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL')
        self.b.book=lambda code:book(48.04,48.22)
        self.assertEqual(self.step.tick(self.s,NOW+timedelta(seconds=65)),'waiting_spread')
        self.assertEqual(self.b.calls,1)
    @reset_test_patch('trading.desktop_worker.ROOT', Path('/nonexistent/test-only-controls'))
    def test_explicit_empty_resume_preserves_guards(self):
        self.s.update(paused=True,alerts=['SafetyError: Spread too wide']);self.db.save(self.s)
        self.assertEqual(resume_empty_probe(self.db,self.b),'chosen')
        self.assertFalse(self.db.load()['paused'])
        self.s['orders']={'x':{'status':'UNKNOWN'}};self.s['paused']=True;self.db.save(self.s)
        with self.assertRaises(SafetyError):resume_empty_probe(self.db,self.b)

if __name__=='__main__':unittest.main()
