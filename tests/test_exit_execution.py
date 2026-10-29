import unittest
from datetime import timedelta
from tests import test_swing as sw
from tests.test_trading import NOW,book
from trading.policy import SafetyError

class ExitExecution(unittest.TestCase):
    setUp=sw.Execution.setUp
    tearDown=sw.Execution.tearDown
    buy=sw.Execution.buy
    def test_wide_stop_detects_and_submits_even_when_valuation_fails(self):
        self.buy();self.b.book=lambda c:book(8,10)
        def unavailable(*a):raise SafetyError('Spread too wide')
        self.e.mark=unavailable
        s=self.e.tick(self.scan,NOW)
        self.assertEqual(s['queue'][0]['exit_reason'],'stop')
        self.e.drain(self.scan,NOW)
        s=self.db.load();o=list(s['orders'].values())[-1]
        self.assertEqual((o['side'],o['price'],o['exit_reason']),('SELL',8,'stop'))
        self.e.drain(self.scan,NOW);self.assertEqual(self.b.calls,2)
        self.e.manage_orders(s,NOW+timedelta(seconds=61))
        self.assertEqual(self.b.remote[-1]['status'],'CANCELLED_ALL')
    def test_target_uses_bid_and_keeps_target_floor(self):
        self.buy();self.b.book=lambda c:book(11.9,13)
        s=self.e.tick(self.scan,NOW);self.assertFalse(s['queue'])
        self.b.book=lambda c:book(12.1,13)
        s=self.e.tick(self.scan,NOW);self.assertEqual(s['queue'][0]['exit_reason'],'target')
        self.b.book=lambda c:book(11.9,13)
        self.e.drain(self.scan,NOW)
        self.assertGreaterEqual(list(self.db.load()['orders'].values())[-1]['price'],12.01)
    def test_scope_and_quote_validity_remain_strict(self):
        item=dict(code='US.A',side='SELL',kind='STOCK',exit_reason='stop')
        for state,it in [(dict(env='REAL'),item),(self.s,dict(item,side='BUY')),(self.s,dict(item,kind='OPTION')),(self.s,dict(item,exit_reason=None))]:
            with self.assertRaises(SafetyError):self.e.execution_quote(state,it,book(8,10),NOW)
        for b in [dict(book(8,10),bid_time=(NOW-timedelta(seconds=31)).isoformat()),dict(book(8,10),ask_time=(NOW+timedelta(seconds=1)).isoformat()),book(10,8)]:
            with self.assertRaises(SafetyError):self.e.execution_quote(self.s,item,b,NOW)
    def test_emergency_exit_preserves_reason(self):
        self.buy();self.e.news=lambda *a:dict(multiplier=0,exit_long=True,exit_evidence=['test'])
        self.b.book=lambda c:book(10,12)
        s=self.e.tick(self.scan,NOW);self.assertEqual(s['queue'][0]['exit_reason'],'emergency_news')
        self.e.drain(self.scan,NOW)
        self.assertEqual(list(self.db.load()['orders'].values())[-1]['exit_reason'],'emergency_news')
