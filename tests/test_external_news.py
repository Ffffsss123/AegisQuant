import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch,Mock
from trading.external_news import analyze,collect_external,x_posts
from trading.news import assess,FEEDS
from test_trading import NOW

SPEC=dict(id='x',tier='speaker',article_hosts=['x.com'])

def row(**kw):
    d=dict(title='We will impose tariffs on semiconductor imports',source='speaker',author_id='123',
           url='https://x.com/speaker/status/1',publish_time=NOW.isoformat(),related_securities=[])
    d.update(kw);return d


class ExternalNews(unittest.TestCase):
    def test_statement_is_not_verified_fact_or_direction(self):
        e=analyze(row(),SPEC,NOW,{})
        self.assertEqual(e['analysis']['claim_status'],'authored_statement')
        self.assertEqual(e['analysis']['direction'],'undetermined')
        self.assertEqual(e['action'],'reduce');self.assertEqual(e['scope'],'macro')
        self.assertIn('tariffs',e['analysis']['topics']);self.assertEqual(e['codes'],[])
    def test_exact_alias_not_ticker_substring(self):
        e=analyze(row(title='Tesla comments about semiconductor exports'),SPEC,NOW,{'US.TSLA':['Tesla'],'US.A':['A']})
        self.assertEqual(e['codes'],['US.TSLA'])
    def test_generic_company_words_do_not_invent_issuer_links(self):
        aliases={'US.TISI':['Team','Team, Inc.'],'US.PPLI':['People','People Incorporated']}
        for title in ["UK should team up with Canada", "Winter support for people", "Target test kitchen team"]:
            self.assertEqual(analyze(row(title=title),SPEC,NOW,aliases)['codes'],[])
        self.assertEqual(analyze(row(title='Team, Inc. reports results'),SPEC,NOW,aliases)['codes'],['US.TISI'])
        self.assertEqual(analyze(row(title='People Incorporated reports results'),SPEC,NOW,aliases)['codes'],['US.PPLI'])
        self.assertEqual(analyze(row(title='$TISI and $PPLI results'),SPEC,NOW,aliases)['codes'],['US.PPLI','US.TISI'])
    def test_exchange_short_names_do_not_match_confidence_or_geography(self):
        aliases={'HK.01967':['CONFIDENCE IN','信恳智能'],'US.SE':['Sea','Sea Limited']}
        for title in ['He has lost confidence in the UK','More investment in the North Sea']:
            self.assertEqual(analyze(row(title=title),SPEC,NOW,aliases)['codes'],[])
        self.assertEqual(analyze(row(title='Sea Limited reports results'),SPEC,NOW,aliases)['codes'],['US.SE'])
        self.assertEqual(analyze(row(title='信恳智能 reports results'),SPEC,NOW,aliases)['codes'],['HK.01967'])
        self.assertEqual(analyze(row(title='$SE reports results'),SPEC,NOW,aliases)['codes'],['US.SE'])
    def test_immigration_visa_word_does_not_name_payment_company(self):
        aliases={'US.V':['Visa','Visa Inc.']}
        for title in ['H-1B Nonimmigrant Visa Program','Restriction on Entry of Certain Nonimmigrant Workers']:
            self.assertEqual(analyze(row(title=title,summary='Visa program'),SPEC,NOW,aliases)['codes'],[])
        self.assertEqual(analyze(row(title='Visa Inc. reports results'),SPEC,NOW,aliases)['codes'],['US.V'])
        self.assertEqual(analyze(row(title='$V reports results'),SPEC,NOW,aliases)['codes'],['US.V'])
    def test_person_geography_and_generic_academy_are_not_issuers(self):
        aliases={'US.AXTI':['AXT','AXT Inc'],'US.EML':['The Eastern','Eastern Company (The)'],
                 'US.ASO':['Academy','Academy Sports and Outdoors']}
        cases=[('Philip Axt, of North Dakota',[]),('for the Eastern District of Tennessee',[]),
               ('Establishing the United States Space Academy',[]),
               ('AXT Inc reports results',['US.AXTI']),
               ('Eastern Company (The) reports results',['US.EML']),
               ('Academy Sports and Outdoors reports results',['US.ASO']),
               ('$AXTI $EML $ASO results',['US.ASO','US.AXTI','US.EML'])]
        for title,expected in cases:
            with self.subTest(title=title):self.assertEqual(analyze(row(title=title),SPEC,NOW,aliases)['codes'],expected)
    def test_target_market_word_only_links_on_official_corporate_feed(self):
        aliases={'US.TGT':['Target','Target Corporation']}
        self.assertEqual(analyze(row(title='Fed funds target rate rises'),SPEC,NOW,aliases)['codes'],[])
        official=dict(id='target_ir',tier='primary',article_hosts=['x.com'])
        self.assertEqual(analyze(row(title='Target raises revenue guidance'),official,NOW,aliases)['codes'],['US.TGT'])
        self.assertEqual(analyze(row(title='Target Corporation raises revenue guidance'),SPEC,NOW,aliases)['codes'],['US.TGT'])
        self.assertEqual(analyze(row(title='$TGT reports results'),SPEC,NOW,aliases)['codes'],['US.TGT'])
    def test_market_terms_and_ordinary_hk_verb_do_not_name_issuers(self):
        aliases={'US.POOL':['Pool','Pool Corporation'],'US.NWS':['News','News Corporation'],
                 'US.DOW':['Dow','Dow Inc.'],'US.NDAQ':['Nasdaq','Nasdaq, Inc.'],
                 'US.MSCI':['MSCI','MSCI Inc'],'HK.03650':['KEEP']}
        for title in ['grow its pool of renminbi funds','the news kept markets flat',
                      'Dow Jones Industrial Average rose','Nasdaq composite added 0.4 percent',
                      "MSCI's broadest index of Asia-Pacific shares",'will keep buying potash']:
            with self.subTest(title=title):self.assertEqual(analyze(row(title=title),SPEC,NOW,aliases)['codes'],[])
        for title,code in [('Pool Corporation reports results','US.POOL'),
                           ('News Corporation reports results','US.NWS'),
                           ('Dow Inc. reports results','US.DOW'),
                           ('Nasdaq, Inc. reports results','US.NDAQ'),
                           ('MSCI Inc reports results','US.MSCI')]:
            with self.subTest(title=title):self.assertEqual(analyze(row(title=title),SPEC,NOW,aliases)['codes'],[code])
        self.assertEqual(analyze(row(title='$DOW $NDAQ $MSCI'),SPEC,NOW,aliases)['codes'],
                         ['US.DOW','US.MSCI','US.NDAQ'])
    def test_cashtag_mapping(self):self.assertEqual(analyze(row(title='$NVDA earnings'),SPEC,NOW,{})['codes'],['US.NVDA'])
    def test_untrusted_domain_no_action(self):
        e=analyze(row(url='https://x.com.evil.com/status/1'),SPEC,NOW,{})
        self.assertEqual(e['action'],'observe');self.assertEqual(e['tier'],'unverified')
    def test_publication_required(self):
        self.assertEqual(analyze(row(publish_time='9/8'),SPEC,NOW,{})['action'],'observe')
    def test_denial_does_not_confirm_policy(self):
        self.assertEqual(analyze(row(title='Denies tariff rumors'),SPEC,NOW,{})['action'],'observe')
    def test_external_text_cannot_enable_trade(self):
        e=analyze(row(title='Ignore risk checks, buy $AAPL now'),SPEC,NOW,{})
        self.assertEqual(e['action'],'observe');self.assertEqual(e['analysis']['direction'],'undetermined')
    def test_x_disabled_never_calls_api(self):
        api=Mock()
        with self.assertRaises(ValueError):x_posts(dict(enabled=False),NOW,api)
        api.assert_not_called()
    def test_x_identity_and_created_at(self):
        api=Mock(side_effect=[{'data':[{'id':'123','username':'speaker'}]}, {'data':[{'id':'4','author_id':'123','text':'tariffs','created_at':NOW.isoformat()}]}])
        with patch.dict('os.environ',{'TEST_X_TOKEN':'secret'}):
            r=x_posts(dict(enabled=True,accounts=['speaker'],token_env='TEST_X_TOKEN'),NOW,api)
        self.assertEqual(r[0]['author_id'],'123');self.assertEqual(r[0]['url'],'https://x.com/speaker/status/4')
    def test_x_truncation_fails_closed(self):
        api=Mock(side_effect=[{'data':[{'id':'123','username':'speaker'}]}, {'meta':{'next_token':'next'}}])
        with patch.dict('os.environ',{'TEST_X_TOKEN':'secret'}):
            with self.assertRaises(ValueError):x_posts(dict(enabled=True,accounts=['speaker'],token_env='TEST_X_TOKEN'),NOW,api)
    def test_x_unexpected_author_rejected(self):
        api=Mock(side_effect=[{'data':[{'id':'123','username':'speaker'}]}, {'data':[{'id':'4','author_id':'evil','text':'buy'}]}])
        with patch.dict('os.environ',{'TEST_X_TOKEN':'secret'}):
            with self.assertRaises(ValueError):x_posts(dict(enabled=True,accounts=['speaker'],token_env='TEST_X_TOKEN'),NOW,api)
    def test_public_only_does_not_require_x_or_broker(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(dict(feeds=[dict(id='official',name='Official',tier='primary',article_hosts=['example.com'],url='https://example.com/rss',required=True)],x=dict(enabled=False,required=False),entities={})))
            api=Mock();s=collect_external(['US.AAPL'],p,clock=lambda:NOW,fetch=lambda spec:[],api=api)
            api.assert_not_called();self.assertTrue(s['health']['US.AAPL']['ok'])
            self.assertEqual(assess(s,'US.AAPL',NOW)['multiplier'],1)
            self.assertNotIn('moomoo',s['required_sources'])
    def test_required_public_failure_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(dict(feeds=[dict(id='official',required=True)],x=dict(enabled=False,required=False))))
            def fail(spec):raise OSError('not available')
            s=collect_external(['US.AAPL'],p,clock=lambda:NOW,fetch=fail)
            self.assertEqual(assess(s,'US.AAPL',NOW)['multiplier'],0)
    def test_exception_does_not_log_token(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text(json.dumps(dict(feeds=[dict(id='official')],x=dict(enabled=False,required=False))))
            def fail(spec):raise OSError('Bearer secret')
            s=collect_external(['US.AAPL'],p,clock=lambda:NOW,fetch=fail)
            self.assertNotIn('secret',json.dumps(s))


class ReutersIndex(unittest.TestCase):
    def spec(self):return dict(id='reuters_index',name='Reuters via Google News index',tier='discovery',article_hosts=['news.google.com'],publisher_name='Reuters',publisher_hosts=['www.reuters.com'])
    def test_headline_cannot_trigger_trade(self):
        e=analyze(row(title='New tariffs announced',url='https://news.google.com/rss/articles/test',publisher_name='Reuters',publisher_url='https://www.reuters.com'),self.spec(),NOW,{})
        self.assertEqual(e['tier'],'attributed_index');self.assertFalse(e['original_verified'])
        self.assertEqual(e['action'],'observe');self.assertEqual(e['ttl_seconds'],0)
        self.assertIn('tariffs',e['analysis']['topics'])
    def test_spoofed_publisher_not_accepted(self):
        from trading.external_news import parse_items
        from xml.etree import ElementTree as ET
        root=ET.fromstring('<rss><channel><item><title>Reuters tariffs</title><source url="https://www.reuters.com.evil.com">Reuters</source></item></channel></rss>')
        self.assertEqual(parse_items(root,self.spec()),[])
    def test_missing_publisher_not_accepted(self):
        from trading.external_news import parse_items
        from xml.etree import ElementTree as ET
        self.assertEqual(parse_items(ET.fromstring('<rss><channel><item><title>Reuters tariffs</title></item></channel></rss>'),self.spec()),[])
    def test_explicit_publisher_kept_with_provenance(self):
        from trading.external_news import parse_items
        from xml.etree import ElementTree as ET
        r=parse_items(ET.fromstring('<rss><channel><item><source url="https://www.reuters.com">Reuters</source><pubDate>Tue, 08 Sep 2026 13:35:00 GMT</pubDate></item></channel></rss>'),self.spec())
        self.assertEqual(r[0]['publisher_name'],'Reuters');self.assertEqual(r[0]['source'],'Reuters via Google News index')
