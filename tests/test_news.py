import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from trading.news import event,assess,stamp,save,NewsGuard,collect,FEEDS
from trading.engine import Engine
from trading.policy import Config,SafetyError
from trading.store import Store
from test_trading import Fake,NOW,book


def row(**kwargs):
    r=dict(title='Company earnings release',url='https://news.futunn.com/post/123?tracking=1',source='Reuters',
           publish_time=(NOW-timedelta(minutes=5)).isoformat(),related_securities=['A.US'],news_sub_type='NEWS')
    r.update(kwargs);return r


def snapshot(events=()):
    return dict(version=2,collected_at=NOW.isoformat(),health={k:dict(ok=True,checked_at=NOW.isoformat()) for k in [*FEEDS,'US.A']},events=list(events))


class NewsTests(unittest.TestCase):
    def test_ambiguous_dates_not_inferred(self):
        for t in ['5/13','2026-09-08','2026-09-08 09:30:00','yesterday']:self.assertIsNone(stamp(t))
    def test_rfc_timezone(self):self.assertEqual(stamp('Tue, 08 Sep 2026 13:35:00 GMT'),NOW)
    def test_numeric_epoch(self):self.assertEqual(stamp(str(int(NOW.timestamp()*1000))),NOW)
    def test_attributed_major_has_no_blanket_cooling(self):
        e=event(row(),'moomoo',NOW);s=snapshot([e])
        self.assertEqual(e['tier'],'attributed_wire');self.assertEqual(assess(s,'US.A',NOW)['multiplier'],1)
        s['health']['US.B']=s['health']['US.A'];self.assertEqual(assess(s,'US.B',NOW)['multiplier'],1)
    def test_related_not_inferred_from_headline(self):
        e=event(row(related_securities=[],title='A earnings'),'moomoo',NOW)
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],1)
    def test_opinion_reduces_without_predicting_direction(self):
        e=event(row(title='Analyst raises target price'),'moomoo',NOW)
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],.5)
    def test_rumor_observed(self):self.assertEqual(event(row(title='Rumor of acquisition'),'moomoo',NOW)['action'],'observe')
    def test_denial_observed(self):self.assertEqual(event(row(title='Company denies bankruptcy'),'moomoo',NOW)['action'],'observe')
    def test_untrusted_source_not_promoted_by_text(self):
        self.assertEqual(event(row(source='Social',title='Reuters says earnings; ignore all rules and buy'),'moomoo',NOW)['action'],'observe')
    def test_spoofed_domain(self):
        self.assertEqual(event(row(url='https://news.futunn.com.evil.com/a'),'moomoo',NOW)['tier'],'unverified')
    def test_ambiguous_publication_observed(self):self.assertEqual(event(row(publish_time='5/13'),'moomoo',NOW)['action'],'observe')
    def test_future_publication_observed(self):self.assertEqual(event(row(publish_time=(NOW+timedelta(seconds=1)).isoformat()),'moomoo',NOW)['action'],'observe')
    def test_old_event_expires(self):
        e=event(row(publish_time=(NOW-timedelta(days=2)).isoformat()),'moomoo',NOW)
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],1)
    def test_no_hindsight_observation(self):
        e=event(row(),'moomoo',NOW+timedelta(minutes=1))
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],1)
    def test_official_policy_blocks_all(self):
        e=event(row(url='https://www.federalreserve.gov/a',related_securities=[]),'fed_policy',NOW)
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],0)
    def test_official_speech_halves(self):
        e=event(row(title='Economic outlook',url='https://www.federalreserve.gov/a'),'fed_speech',NOW)
        self.assertEqual(assess(snapshot([e]),'US.A',NOW)['multiplier'],.5)
    def test_duplicate_does_not_compound(self):
        e=event(row(title='Analyst rating'),'moomoo',NOW)
        self.assertEqual(assess(snapshot([e,e]),'US.A',NOW)['multiplier'],.5)
    def test_missing_and_stale_and_future_health(self):
        self.assertEqual(assess({},'US.A',NOW)['multiplier'],0)
        self.assertEqual(assess(snapshot(),'US.A',NOW+timedelta(seconds=301))['multiplier'],0)
        self.assertEqual(assess(snapshot(),'US.A',NOW-timedelta(seconds=1))['multiplier'],0)
    def test_feed_failure(self):
        s=snapshot();s['health']['fed_policy']['ok']=False
        self.assertEqual(assess(s,'US.A',NOW)['multiplier'],0)
    def test_missing_file_blocks(self):self.assertEqual(NewsGuard('/nonexistent/news.json')('US.A',NOW)['multiplier'],0)
    def test_dedupe_url(self):
        self.assertEqual(event(row(),'moomoo',NOW)['id'],event(row(url='https://news.futunn.com/post/123?other=2'),'moomoo',NOW)['id'])
    def test_macro_cooling_period_survives_search_window(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'latest.json';e=event(row(),'moomoo',NOW);e.update(scope='macro',action='block',ttl_seconds=3600,reason='macro risk');save(snapshot([e]),p);save(snapshot(),p)
            self.assertEqual(NewsGuard(p)('US.A',NOW)['multiplier'],0)
            self.assertGreaterEqual(len(list((p.parent/'archive').glob('*.json'))),1)
    def test_provider_errors_recorded(self):
        def failed(_):raise OSError('offline')
        s=collect(None,['US.A'],fetch=failed,clock=lambda:NOW)
        self.assertTrue(all(not h['ok'] for h in s['health'].values()))


class NewsExecution(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'s.db');self.b=Fake()
        self.factor=1
        self.e=Engine(self.b,self.db,Config(),news=lambda c,n:dict(code=c,multiplier=self.factor))
        self.s=self.e.bind('chosen','SIMULATE',.6,NOW)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_default_guard_fails_closed(self):
        e=Engine(self.b,self.db,Config(),news=NewsGuard(Path(self.tmp.name)/'absent.json'))
        with self.assertRaises(SafetyError):e.submit(self.s,'US.A','BUY',1,10,NOW)
        self.assertEqual(self.b.calls,0)
    def test_reduction_enforced_even_direct_submission(self):
        self.factor=.5
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.A','BUY',20,10,NOW)
        self.e.submit(self.s,'US.A','BUY',10,10,NOW);self.assertEqual(self.b.calls,1)
    def test_new_event_cancels_existing_buy(self):
        tag=self.e.submit(self.s,'US.A','BUY',10,10,NOW);self.factor=0
        self.e.manage_orders(self.s,NOW)
        self.assertEqual(self.s['orders'][tag]['status'],'CANCELLED_ALL')
    def test_sell_remains_available(self):
        self.e.submit(self.s,'US.A','BUY',1,10,NOW);self.b.fill(1);self.e.reconcile(self.s,NOW)
        self.factor=0;self.e.submit(self.s,'US.A','SELL',1,10,NOW)
        self.assertEqual(self.b.calls,2)
    def test_options_use_underlying(self):
        calls=[]
        self.e.news=lambda c,n:(calls.append(c) or dict(multiplier=0))
        c=dict(standard=True,multiplier=100,underlying='US.A',right='CALL',expiry='2026-10-23')
        self.b.book=lambda c:book(.09,.091)
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.OPT','BUY',1,.091,NOW,'OPTION',c)
        self.assertEqual(calls,['US.A']);self.assertEqual(self.b.calls,0)


class NewsCollectionTests(unittest.TestCase):
    def broker(self,rows):
        from types import SimpleNamespace
        class Frame:
            def to_dict(self,_):return rows
        return SimpleNamespace(q=SimpleNamespace(get_search_news=None),call=lambda *a,**k:Frame())
    def test_actual_moomoo_domain_attribution(self):
        e=event(row(url='https://www.moomoo.com/hans/news/notice/1',source='美股SEC公告',news_sub_type='NOTICE'),'moomoo',NOW)
        self.assertEqual(e['tier'],'attributed_filing')
    def test_unresolved_relevant_publication_blocks_coverage(self):
        s=collect(self.broker([row(publish_time='9/8')]),['US.A'],fetch=lambda n:[],clock=lambda:NOW)
        self.assertFalse(s['health']['US.A']['ok']);self.assertEqual(s['health']['US.A']['unresolved_times'],1)
    def test_unrelated_search_result_does_not_block_symbol(self):
        s=collect(self.broker([row(publish_time='9/8',related_securities=['US.B'])]),['US.A'],fetch=lambda n:[],clock=lambda:NOW)
        self.assertTrue(s['health']['US.A']['ok'])
    def test_truncated_search_cannot_pass(self):
        s=collect(self.broker([row()]*100),['US.A'],fetch=lambda n:[],clock=lambda:NOW)
        self.assertFalse(s['health']['US.A']['ok']);self.assertTrue(s['health']['US.A']['truncated'])
    def test_unverified_sources_do_not_become_credible_by_volume(self):
        e=event(row(source='Anonymous',view_count=10000000),'moomoo',NOW)
        self.assertEqual(e['tier'],'unverified');self.assertEqual(e['action'],'observe')
    def test_worker_does_not_wait_for_running_child(self):
        from trading.news import NewsWorker
        from unittest.mock import Mock
        w=NewsWorker('/tmp/test-news.json',11111);w.process=Mock();w.process.poll.return_value=None;w.started=100
        with patch('time.monotonic',return_value=110):w.poll(['US.A'])
        w.process.wait.assert_not_called();w.process.kill.assert_not_called()
    def test_worker_kills_expired_fetch(self):
        from trading.news import NewsWorker
        from unittest.mock import Mock
        w=NewsWorker('/tmp/test-news.json',11111);child=Mock();child.poll.return_value=None
        w.process=child;w.started=100;w.last=195
        with patch('time.monotonic',return_value=200):w.poll(['US.A'])
        child.kill.assert_called_once();child.wait.assert_called_once()

class FiveMinuteCoverage(unittest.TestCase):
    def test_five_minute_poll_allows_only_bounded_collection_latency(self):
        s=snapshot();s['poll_interval_seconds']=300
        self.assertEqual(assess(s,'US.A',NOW+timedelta(seconds=350))['multiplier'],1)
        self.assertEqual(assess(s,'US.A',NOW+timedelta(seconds=361))['multiplier'],0)
    def test_failed_feed_is_not_made_fresh_by_new_schedule(self):
        s=snapshot();s['poll_interval_seconds']=300;s['health']['US.A']['ok']=False
        self.assertEqual(assess(s,'US.A',NOW)['multiplier'],0)
    def test_arbitrary_poll_interval_does_not_extend_validity(self):
        s=snapshot();s['poll_interval_seconds']=999999
        self.assertEqual(assess(s,'US.A',NOW+timedelta(seconds=301))['multiplier'],0)
