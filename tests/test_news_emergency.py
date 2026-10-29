import copy,unittest
from datetime import timedelta
from unittest.mock import Mock
from trading.news_emergency import annotate,exit_evidence
from trading.external_news import analyze
from trading.news import assess
from tests import test_swing as sw,test_options_observer as op
from tests.test_trading import NOW

SPEC=dict(id='bbc_business',tier='newsroom',article_hosts=['www.bbc.com'])
ENTITIES={'US.A':['Acme'],'US.B':['Beta']}
def verified(title='Acme files for bankruptcy'):
 row=dict(title=title,url='https://www.bbc.com/news/articles/test',source='BBC News',publish_time=NOW.isoformat(),related_securities=[])
 e=analyze(row,SPEC,NOW,ENTITIES)
 return annotate(e,SPEC,ENTITIES,NOW,fetch=lambda *args:(title+' - BBC News','sha256-test'))
def snap(e):return dict(version=3,required_sources=['bbc_business'],collected_at=NOW.isoformat(),health={k:dict(ok=True,checked_at=NOW.isoformat()) for k in ['bbc_business','US.A']},events=[e])
class Classifier(unittest.TestCase):
 def test_direct_report_and_original_match(self):
  s=snap(verified());d=assess(s,'US.A',NOW);self.assertTrue(d['exit_long']);self.assertFalse(d['exit_short']);self.assertEqual(d['multiplier'],0)
 def test_other_stock_is_not_sold(self):self.assertFalse(exit_evidence(snap(verified()),'US.B',NOW))
 def test_three_supported_catalysts(self):
  for title in ['Acme files for bankruptcy','Acme defaults on its debt','Acme cuts annual profit guidance']:
   self.assertIn('emergency_exit',verified(title))
 def test_denial_rumor_hypothetical_unrelated_subject(self):
  for title in ['Acme denies bankruptcy','Acme may file for bankruptcy','Acme files for bankruptcy?','Acme supplier files for bankruptcy','Acme avoids bankruptcy','Acme raises profit guidance','Acme cuts jobs','Acme cuts annual profit guidance if demand falls']:
   self.assertNotIn('emergency_exit',verified(title))
 def test_original_mismatch_or_unavailable(self):
  e=verified();e.pop('emergency_exit')
  for fetch in [lambda *args:('Other news','hash'),Mock(side_effect=OSError('offline'))]:
   self.assertNotIn('emergency_exit',annotate(copy.deepcopy(e),SPEC,ENTITIES,NOW,fetch))
 def test_index_speaker_and_unverified_cannot_exit(self):
  for tier in ['attributed_index','speaker','unverified']:
   e=verified();e.pop('emergency_exit');e['tier']=tier;fetch=Mock()
   self.assertNotIn('emergency_exit',annotate(e,SPEC,ENTITIES,NOW,fetch));fetch.assert_not_called()
 def test_old_future_or_failed_source_cannot_trigger(self):
  for key,value in [('published_at',(NOW-timedelta(minutes=31)).isoformat()),('observed_at',(NOW+timedelta(seconds=1)).isoformat())]:
   e=verified();e[key]=value;self.assertFalse(exit_evidence(snap(e),'US.A',NOW))
  s=snap(verified());s['health']['bbc_business']['ok']=False;self.assertFalse(exit_evidence(s,'US.A',NOW))
 def test_stale_cache_does_not_create_new_exit(self):self.assertFalse(exit_evidence(snap(verified()),'US.A',NOW+timedelta(minutes=6)))
 def test_news_block_is_not_itself_a_sell(self):
  e=verified('Acme earnings release');self.assertFalse(assess(snap(e),'US.A',NOW)['exit_long'])

class StockExits(unittest.TestCase):
 setUp=sw.Execution.setUp
 tearDown=sw.Execution.tearDown
 def prepare(self):
  sw.Execution.buy(self);self.e.news=lambda *args:dict(multiplier=0,exit_long=True,exit_evidence=[{'event_id':'event'}])
 def test_exit_before_loss_threshold_and_outside_entry_window(self):
  self.prepare();now=NOW+timedelta(hours=1);self.b.now_value=now;s=self.e.tick(self.scan,now)
  self.assertFalse(s['paused']);self.assertEqual(s['queue'][0]['exit_reason'],'emergency_news');self.e.drain(self.scan,now)
  self.assertEqual(self.b.calls,2)
 def test_latched_exit_survives_restart_and_news_expiry(self):
  self.prepare();self.e.tick(self.scan,NOW)
  e=sw.SwingEngine(self.b,self.db,sw.Config(),news=lambda *args:dict(multiplier=1))
  s=e.tick(self.scan,NOW);self.assertEqual(len(s['queue']),1);self.assertIn('US.A',s['emergency_exits'])
 def test_pause_allows_emergency_exit_stop_does_not(self):
  self.prepare();self.s=self.db.load();self.s['paused']=True;self.db.save(self.s)
  self.assertEqual(self.e.tick(self.scan,NOW)['queue'][0]['exit_reason'],'emergency_news')
  self.s=self.db.load();self.s['stopped']=True;self.db.save(self.s);self.e.drain(self.scan,NOW);self.assertEqual(self.b.calls,1)
 def test_missing_history_does_not_block_news_exit(self):
  self.prepare();s=self.e.tick(dict(self.scan,signals={},complete=False),NOW);self.assertEqual(s['queue'][0]['exit_reason'],'emergency_news')
 def test_bad_exit_quote_does_not_fabricate_fill(self):
  self.prepare();self.e.tick(self.scan,NOW);self.b.quote_bad=True;self.e.drain(self.scan,NOW)
  s=self.db.load();self.assertEqual(self.b.calls,1);self.assertEqual(s['positions']['US.A']['qty'],2);self.assertIn('US.A',s['emergency_exits'])

class OptionExits(unittest.TestCase):
 setUp=op.Paper.setUp
 tearDown=op.Paper.tearDown
 enter=op.Paper.enter
 def test_adverse_news_closes_call_on_bid(self):
  self.enter();self.e.news=lambda *args:dict(multiplier=0,exit_long=True,exit_evidence=[{'event_id':'event'}])
  self.e.position(self.s,op.parent(),op.NOW,{})
  self.assertIsNone(self.s['position']);self.assertEqual(self.s['trades'][-1]['reason'],'emergency_news')
 def test_bearish_news_is_not_automatically_bad_for_put(self):
  self.enter();self.s['position']['right']='PUT';scan=op.parent();scan['signals']['US.TEST'].update(fast=9,slow=10)
  self.e.news=lambda *args:dict(multiplier=0,exit_long=True,exit_short=False)
  self.e.position(self.s,scan,op.NOW,{})
  self.assertIsNotNone(self.s['position'])

class DirectoryNames(unittest.TestCase):
 def test_exchange_names_map_corporate_subjects_without_suffix(self):
  import tempfile
  from pathlib import Path
  from trading.news_emergency import issuer_aliases
  with tempfile.TemporaryDirectory() as root:
   d=Path(root)/'directory';d.mkdir();(d/'OTHER.txt').write_text('ACT Symbol|Security Name\nCVI|CVR Energy Inc. Common Stock\nX|Other Inc. Common Stock\n')
   rows=issuer_aliases(['US.CVI'],root);self.assertIn('CVR Energy',rows['US.CVI']);self.assertNotIn('US.X',rows)
