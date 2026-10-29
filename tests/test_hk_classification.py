import unittest,tempfile,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from trading.hk_classification import reviewed_class
from trading.policy import SafetyError
class HKClassification(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);p=self.root/'classification_sources';p.mkdir();self.pdf=p/'source.pdf';self.pdf.write_bytes(b'%PDF fixture')
        self.now=datetime(2026,9,18,tzinfo=timezone.utc);self.meta=dict(code='HK.09678',isin='identity')
        self.record=dict(code='HK.09678',isin='identity',kind='COMMON',listed_on_exchange=True,status='source_reviewed_not_integrated',period_end='2026-08-31',submitted='2026-09-01',reviewed_at=self.now.isoformat(),source='https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0901/source.pdf',document_path=str(self.pdf),source_document_sha256=hashlib.sha256(self.pdf.read_bytes()).hexdigest())
    def tearDown(self):self.tmp.cleanup()
    def check(self):
        (self.root/'classification_review.json').write_text(json.dumps(dict(records={'HK.09678':self.record})))
        return reviewed_class(self.root,'HK.09678',self.meta,self.now)
    def test_review_is_not_order_permission(self):
        self.assertTrue(self.check()['share_class_reviewed']);self.assertFalse(self.check()['orders_enabled'])
    def test_wrong_security_or_old_period_fails(self):
        self.meta['isin']='changed'
        with self.assertRaises(SafetyError):self.check()
        self.meta['isin']='identity';self.record['period_end']='2026-07-31'
        with self.assertRaises(SafetyError):self.check()
    def test_modified_document_rejected(self):
        self.pdf.write_bytes(b'changed')
        with self.assertRaises(SafetyError):self.check()
