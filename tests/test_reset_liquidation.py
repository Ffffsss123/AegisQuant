import tempfile,unittest
from pathlib import Path
from tests.test_trading import Engine,Fake,NOW
from trading.store import Store
from trading.policy import Config,SafetyError
from trading.rolling_scan import atomic
from trading.reset_liquidation import step
from trading.desktop_server import read_json
class Reset(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=Store(self.root/'data/trading/paper.sqlite');self.b=Fake();self.e=Engine(self.b,self.db,Config());self.e.controls=lambda:dict(paused=True,reset_pending=True)
  self.s=self.e.bind('chosen','SIMULATE',.6,NOW);self.s['positions']={'US.A':dict(qty=1,kind='STOCK',multiplier=1,cost=10,realized=0,fees=0)};self.s['cash']-=10;self.s['paused']=True;self.b.pos={'US.A':1};self.db.save(self.s)
  self.req=dict(id='test',liquidate=True,env='SIMULATE',account='chosen',bound_at=self.s['bound_at'],history_start=NOW.date().isoformat(),known_order_ids=[],positions={'US.A':1})
  atomic(self.root/'data/trading/reset_request.json',self.req)
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_closed_session_no_order(self):
  self.b.execution_session=lambda *a,**k:None;step(self.e,self.s,NOW,self.root);self.assertEqual(self.b.calls,0)
 def test_sell_with_pause_retains_history_until_flat(self):
  step(self.e,self.s,NOW,self.root);self.assertEqual(self.b.calls,1);self.assertEqual(next(iter(self.s['orders'].values()))['execution_purpose'],'user_reset')
  self.b.fill(1,10);step(self.e,self.s,NOW,self.root);self.assertEqual(read_json(self.root/'data/trading/reset_status.json')['status'],'flat_ready_for_archive');self.assertEqual(len(self.db.load()['orders']),1)
 def test_unknown_inventory_blocks(self):
  self.b.pos['US.B']=1
  with self.assertRaises(SafetyError):step(self.e,self.s,NOW,self.root)
  self.assertEqual(self.b.calls,0)
 def test_cancelled_exit_not_reissued(self):
  step(self.e,self.s,NOW,self.root);self.b.cancel('1');step(self.e,self.s,NOW,self.root);self.assertEqual(self.b.calls,1);self.assertEqual(read_json(self.root/'data/trading/reset_status.json')['status'],'needs_attention')
 def test_stop_preserved(self):
  self.e.controls=lambda:dict(stopped=True);step(self.e,self.s,NOW,self.root);self.assertEqual(self.b.calls,0)
 def test_timeout_queries_existing_order_then_cancels_without_resubmit(self):
  from datetime import timedelta
  self.b.fail=True
  step(self.e,self.s,NOW,self.root)
  self.assertEqual(next(iter(self.s['orders'].values()))['status'],'UNKNOWN')
  step(self.e,self.s,NOW+timedelta(seconds=61),self.root)
  self.assertEqual(self.b.calls,1)
  self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL')
  self.assertEqual(self.s['positions']['US.A']['qty'],1)
  self.assertEqual(read_json(self.root/'data/trading/reset_status.json')['status'],'needs_attention')
 def test_unknown_remote_order_prevents_flat_completion(self):
  self.s['positions']['US.A']['qty']=0;self.b.pos={}
  self.b.remote=[dict(id='external',code='US.B',side='BUY',qty=1,filled=0,average=0,status='CANCELLED_ALL',tag='external')]
  with self.assertRaises(SafetyError):step(self.e,self.s,NOW,self.root)
  self.assertFalse(read_json(self.root/'data/trading/reset_status.json').get('reset_complete'))
  self.assertEqual(self.b.calls,0)
 def test_reset_exit_does_not_bypass_quote_age_or_spread(self):
  from datetime import timedelta
  for book in [dict(bid=9,ask=10,bid_time=NOW.isoformat(),ask_time=NOW.isoformat()),dict(bid=10,ask=10.01,bid_time=(NOW-timedelta(seconds=31)).isoformat(),ask_time=NOW.isoformat())]:
   self.b.book=lambda code,row=book:row
   with self.assertRaises(SafetyError):step(self.e,self.s,NOW,self.root)
   self.assertEqual(self.b.calls,0);self.assertFalse(self.s['orders'])
   self.assertEqual(read_json(self.root/'data/trading/reset_status.json')['status'],'waiting_quote')
 def test_authorization_for_another_binding_cannot_liquidate(self):
  self.req['bound_at']='2026-01-01T00:00:00+00:00';atomic(self.root/'data/trading/reset_request.json',self.req)
  with self.assertRaises(SafetyError):step(self.e,self.s,NOW,self.root)
  self.assertEqual(self.b.calls,0)
