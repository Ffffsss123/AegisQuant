import unittest,tempfile,json,hashlib
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime,timezone,timedelta
from trading.hk_news import identities,SOURCE
from trading.external_news import collect_external
from trading.news import assess
from trading.news_identity import specific_alias

class HKIssuerNewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=datetime(2026,9,17,3,tzinfo=timezone.utc)
        raw=b'fixture official document';(self.root/'ListOfSecurities.xlsx').write_bytes(raw)
        self.catalog=dict(source=SOURCE,source_date='2026-09-17',sha256=hashlib.sha256(raw).hexdigest(),rows={
            'HK.00700':dict(code='HK.00700',currency='HKD',name_en='TENCENT',name_zh='腾讯控股',lot_size=100)})
        self.basic=dict(queried_at=self.now.isoformat(),rows=[dict(code='HK.00700',name='腾讯控股',lot_size=100,delisting=False)])
        self.save()
    def save(self):
        (self.root/'catalog.json').write_text(json.dumps(self.catalog));(self.root/'basic.json').write_text(json.dumps(self.basic))
    def tearDown(self):self.tmp.cleanup()
    def collect(self,fail_hk=False,identity=None):
        config=dict(feeds=[dict(id='global',required=True),dict(id='hk',tier='newsroom',markets=['HK'],required=False)],x=dict(enabled=False,required=False),entities={})
        p=self.root/'feeds.json';p.write_text(json.dumps(config))
        def fetch(spec):
            if fail_hk and spec['id']=='hk':raise OSError('offline')
            return []
        return collect_external(['US.AAPL','HK.00700'],p,clock=lambda:self.now,fetch=fetch,
            hk_identities=identities(self.root,['HK.00700'],self.now) if identity is None else identity)
    def test_identity_and_local_feed_both_required(self):
        snapshot=self.collect();self.assertEqual(assess(snapshot,'HK.00700',self.now)['multiplier'],1)
        self.assertIn('not exhaustive',snapshot['health']['HK.00700']['coverage'])
        self.assertEqual(assess(self.collect(identity={}),'HK.00700',self.now)['multiplier'],0)
    def test_hk_feed_failure_does_not_disable_us_coverage(self):
        snapshot=self.collect(fail_hk=True)
        self.assertEqual(assess(snapshot,'HK.00700',self.now)['multiplier'],0)
        self.assertEqual(assess(snapshot,'US.AAPL',self.now)['multiplier'],1)
    def test_changed_document_or_stale_catalog_not_accepted(self):
        self.assertTrue(identities(self.root,['HK.00700'],self.now))
        (self.root/'ListOfSecurities.xlsx').write_bytes(b'changed')
        self.assertEqual(identities(self.root,['HK.00700'],self.now),{})
        self.assertEqual(identities(self.root,['HK.00700'],self.now+timedelta(days=1)),{})
    def test_mismatched_issuer_name_or_lot_not_accepted(self):
        self.basic['rows'][0]['name']='其他公司';self.save()
        self.assertEqual(identities(self.root,['HK.00700'],self.now),{})
        self.basic['rows'][0]['name']='腾讯控股';self.basic['rows'][0]['lot_size']=200;self.save()
        self.assertEqual(identities(self.root,['HK.00700'],self.now),{})
    def test_reviewed_full_name_restores_precise_short_issuer_match(self):
        code='HK.03650';isin='KYG5244R1083'
        self.catalog['rows'][code]=dict(code=code,currency='HKD',name_en='KEEP',name_zh='KEEP',lot_size=100,isin=isin)
        self.basic['rows'].append(dict(code=code,name='KEEP',lot_size=100,delisting=False));self.save()
        source=self.root/'classification_sources';source.mkdir()
        pdf=source/'keep.pdf';pdf.write_bytes(b'official issuer: Keep Inc.')
        review=dict(code=code,isin=isin,source='https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0903/keep.pdf',
            source_document_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),document_path=str(pdf),
            period_end='2026-08-31',submitted='2026-09-03',reviewed_at=self.now.isoformat(),
            kind='COMMON',listed_on_exchange=True,status='source_reviewed_not_integrated',
            issuer_name_en='Keep Inc.',issuer_name_page=1)
        (self.root/'classification_review.json').write_text(json.dumps(dict(records={code:review})))
        with patch('trading.hk_news.subprocess.run',return_value=SimpleNamespace(
                stdout='Name of Issuer:\n\nKeep Inc. (Incorporated in Cayman Islands)\n\nDate Submitted:')):
            record=identities(self.root,[code],self.now)[code]
            self.assertIn('Keep Inc.',record['aliases'])
            self.assertFalse(specific_alias('KEEP',provider='rthk_finance',code=code))
            self.assertTrue(specific_alias('Keep Inc.',provider='rthk_finance',code=code))
            review['period_end']='2026-07-31'
            (self.root/'classification_review.json').write_text(json.dumps(dict(records={code:review})))
            self.assertIn('Keep Inc.',identities(self.root,[code],self.now)[code]['aliases'])
            with patch('trading.hk_news.subprocess.run',return_value=SimpleNamespace(
                    stdout='Name of Issuer:\nOther Inc.\nDate Submitted:')):
                self.assertNotIn('Keep Inc.',identities(self.root,[code],self.now)[code]['aliases'])
            pdf.write_bytes(b'changed')
            self.assertNotIn('Keep Inc.',identities(self.root,[code],self.now)[code]['aliases'])
