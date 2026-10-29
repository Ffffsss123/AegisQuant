import copy,unittest,tempfile
from pathlib import Path
from datetime import timedelta
from trading.news_opportunity import annotate,entry_evidence
from trading.external_news import analyze
from trading.news import assess
from trading.engine import Engine
from trading.store import Store
from trading.policy import Config,SafetyError
from tests.test_trading import Fake,NOW
from tests.test_news_emergency import SPEC,ENTITIES,snap

def event(title='Acme raises annual profit guidance'):
    row=dict(title=title,url='https://www.bbc.com/news/articles/test',source='BBC News',publish_time=NOW.isoformat(),related_securities=[])
    return annotate(analyze(row,SPEC,NOW,ENTITIES),SPEC,ENTITIES,NOW,fetch=lambda *a:(title,'digest'))

class Opportunity(unittest.TestCase):
    def test_original_positive_evidence(self):
        d=assess(snap(event()),'US.A',NOW)
        self.assertEqual(d['entry_evidence'][0]['kind'],'guidance_raise')
        self.assertFalse(d['exit_long'])
    def test_verified_opportunity_does_not_override_macro_block(self):
        snapshot=snap(event())
        snapshot['events'].append(dict(id='macro',scope='macro',action='block',ttl_seconds=3600,reason='macro risk',published_at=NOW.isoformat(),observed_at=NOW.isoformat()))
        decision=assess(snapshot,'US.A',NOW)
        self.assertTrue(decision['entry_evidence'])
        self.assertEqual(decision['multiplier'],0)
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'test.sqlite');broker=Fake()
            try:
                engine=Engine(broker,db,Config(),news=lambda code,now:assess(snapshot,code,now))
                engine.controls=lambda:dict(require_news_opportunity=True)
                state=engine.bind('chosen','SIMULATE',.6,NOW)
                with self.assertRaisesRegex(SafetyError,'News risk filter'):
                    engine.submit(state,'US.A','BUY',1,10.01,NOW)
                self.assertEqual(broker.calls,0)
                self.assertFalse(db.load()['orders'])
                # The independent preparation path must preserve the same real
                # policy conflict; a positive event is not a bypass token.
                from contextlib import contextmanager
                from unittest.mock import patch
                from decimal import Decimal
                from trading.option_transport import prepare_independent_contract
                @contextmanager
                def admission(*args,**kwargs):
                    yield {},dict(available_usd=Decimal(500),required_usd=Decimal(120)),{'US_OPTION':engine}
                with patch('trading.simulation_admission.admitted_us_session',admission),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract') as market, patch('trading.option_transport.datetime') as clock:
                    clock.now.return_value=NOW
                    with self.assertRaisesRegex(SafetyError,'News risk filter'):
                        prepare_independent_contract(Path(root),broker,{},account='chosen',hk_account='hk',portfolio='p',archive={},risk_path='unused',contract={'underlying':'US.A'},required_usd=120)
                    market.assert_not_called()
                self.assertEqual(broker.calls,0)
                row=db.db.execute("SELECT body FROM events WHERE kind='independent_option_news_decision' ORDER BY id DESC LIMIT 1").fetchone()
                import json
                recorded=json.loads(row[0]);self.assertTrue(recorded['entry_evidence']);self.assertEqual(recorded['multiplier'],0)
            finally:db.close()

    def test_unsupported_and_hedged_titles(self):
        for title in ['Acme may raise profit guidance','Acme supplier raises profit guidance','Acme cuts profit guidance','Acme raises profit guidance if demand grows']:
            self.assertFalse(entry_evidence(snap(event(title)),'US.A',NOW))
    def test_stale_wrong_symbol_failed_provider(self):
        s=snap(event())
        self.assertFalse(entry_evidence(s,'US.B',NOW))
        self.assertFalse(entry_evidence(s,'US.A',NOW+timedelta(minutes=31)))
        s['health']['bbc_business']['ok']=False
        self.assertFalse(entry_evidence(s,'US.A',NOW))
    def test_index_is_not_original_evidence(self):
        e=event();e.pop('entry_opportunity');e['tier']='attributed_index'
        self.assertNotIn('entry_opportunity',annotate(e,SPEC,ENTITIES,NOW))
    def test_entry_enforced_before_intent_and_evidence_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'test.sqlite');b=Fake()
            try:
                decision=dict(multiplier=1,entry_evidence=[])
                e=Engine(b,db,Config(),news=lambda *a:copy.deepcopy(decision))
                e.controls=lambda:dict(require_news_opportunity=True)
                s=e.bind('chosen','SIMULATE',.6,NOW)
                with self.assertRaisesRegex(SafetyError,'news opportunity'):e.submit(s,'US.A','BUY',1,10.01,NOW)
                self.assertEqual(b.calls,0);self.assertFalse(s['orders'])
                decision['entry_evidence']=entry_evidence(snap(event()),'US.A',NOW)
                tag=e.submit(s,'US.A','BUY',1,10.01,NOW)
                self.assertEqual(db.load()['orders'][tag]['entry_news_evidence'][0]['event_id'],event()['id'])
                b.fill(1,10.01);e.reconcile(s,NOW)
                decision['entry_evidence']=[]
                e.submit(s,'US.A','SELL',1,10,NOW)
                self.assertEqual(b.calls,2)
            finally:db.close()

    def test_cooling_then_fresh_original_reverification(self):
        now=NOW+timedelta(hours=24,minutes=1)
        e=event()
        s=snap(e)
        s['collected_at']=now.isoformat()
        for h in s['health'].values():h['checked_at']=now.isoformat()
        self.assertFalse(assess(s,'US.A',now)['entry_evidence'])
        e=annotate(e,SPEC,ENTITIES,now,fetch=lambda *a:(e['title'],'new-digest'))
        s['events']=[e]
        decision=assess(s,'US.A',now)
        self.assertEqual(decision['multiplier'],1)
        self.assertTrue(decision['entry_evidence'])
        later=now+timedelta(minutes=31)
        s['collected_at']=later.isoformat()
        for h in s['health'].values():h['checked_at']=later.isoformat()
        self.assertFalse(assess(s,'US.A',later)['entry_evidence'])
        expired=NOW+timedelta(hours=48,seconds=1)
        e.pop('entry_opportunity')
        self.assertNotIn('entry_opportunity',annotate(e,SPEC,ENTITIES,expired,fetch=lambda *a:(e['title'],'digest')))

    def test_revoked_cached_cooling_does_not_block_verified_opportunity(self):
        e=event()
        e.update(action='block',ttl_seconds=86400,reason='External major-event risk flag: entry cooling period; direction unverified')
        d=assess(snap(e),'US.A',NOW)
        self.assertEqual(d['multiplier'],1)
        self.assertTrue(d['entry_evidence'])
        from trading.news_emergency import annotate as negative
        bad=analyze(dict(title='Acme files for bankruptcy',url=e['url'],source='BBC News',publish_time=NOW.isoformat(),related_securities=[]),SPEC,NOW,ENTITIES)
        bad=negative(bad,SPEC,ENTITIES,NOW,fetch=lambda *a:(bad['title'],'digest'))
        d=assess(snap(bad),'US.A',NOW)
        self.assertTrue(d['exit_long'])
        self.assertEqual(d['multiplier'],0)
