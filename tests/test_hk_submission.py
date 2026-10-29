import unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone
from trading.hk_submission import SubmissionJournal
from trading.policy import SafetyError

class HKSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'sub.sqlite'
        self.journal=SubmissionJournal(self.path,'8653');self.now=datetime.now(timezone.utc)
        self.request=dict(account='8653',env='SIMULATE',market='HK',currency='HKD',side='BUY',
            order_type='NORMAL',time_in_force='DAY',tag='HK-SIM-test',code='HK.00001',qty=100,price=10)
        self.row=dict(remark='HK-SIM-test',order_id='123',code='HK.00001',trd_side='BUY',qty=100,price=10)
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def reconcile(self,rows,**kw):
        args=dict(account='8653',return_code=0,rows=rows,evidence='raw-query');args.update(kw)
        return self.journal.reconcile(self.request['tag'],**args)
    def test_readonly_open_never_initializes_or_claims(self):
        import sqlite3
        missing=self.path.parent/'missing.sqlite'
        with self.assertRaises(sqlite3.OperationalError):SubmissionJournal.open_existing_readonly(missing,'8653')
        self.assertFalse(missing.exists())
        with self.assertRaises(SafetyError):SubmissionJournal.open_existing_readonly(self.path,'8652')
        reader=SubmissionJournal.open_existing_readonly(self.path,'8653')
        try:
            self.assertEqual(reader.db.execute('SELECT count(*) FROM submissions').fetchone()[0],0)
            with self.assertRaises(sqlite3.OperationalError):reader.claim(self.request,self.now)
            self.assertEqual(self.journal.db.execute('SELECT count(*) FROM submissions').fetchone()[0],0)
        finally:reader.close()

    def test_restart_after_timeout_cannot_resend_even_when_order_missing(self):
        self.journal.claim(self.request,self.now);self.journal.close()
        self.journal=SubmissionJournal(self.path,'8653')
        with self.assertRaises(SafetyError):self.reconcile([])
        with self.assertRaises(SafetyError):self.journal.claim(self.request,self.now)
        self.assertEqual(self.reconcile([self.row]),'123')
        self.assertEqual(self.reconcile([self.row]),'123')
        with self.assertRaises(SafetyError):self.journal.claim(self.request,self.now)
    def test_duplicate_tags_and_changed_order_identity_block(self):
        self.journal.claim(self.request,self.now)
        with self.assertRaises(SafetyError):self.reconcile([self.row,self.row])
        for k,v in [('code','HK.00002'),('qty',200),('price',11),('trd_side','SELL')]:
            with self.assertRaises(SafetyError):self.reconcile([dict(self.row,**{k:v})])
        self.reconcile([self.row])
        with self.assertRaises(SafetyError):self.reconcile([dict(self.row,order_id='456')])
    def test_wrong_account_and_failed_query_do_not_resolve(self):
        self.journal.claim(self.request,self.now)
        with self.assertRaises(SafetyError):self.reconcile([self.row],account='7929')
        with self.assertRaises(SafetyError):self.reconcile([self.row],return_code=-1)
        with self.assertRaises(SafetyError):SubmissionJournal(self.path,'7929')
    def test_one_order_cannot_cover_two_claims(self):
        self.journal.claim(self.request,self.now);self.reconcile([self.row])
        second=dict(self.request,tag='HK-SIM-second');self.journal.claim(second,self.now)
        with self.assertRaises(SafetyError):
            self.journal.reconcile(second['tag'],account='8653',return_code=0,
                rows=[dict(self.row,remark=second['tag'])],evidence='raw-query')

    def test_sell_cannot_claim_unknown_or_other_security_entry(self):
        self.journal.claim(self.request,self.now)
        for key,code in [('missing','HK.00001'),(self.request['tag'],'HK.00002')]:
            with self.assertRaises(SafetyError):
                self.journal.claim(dict(self.request,side='SELL',tag='HK-SIM-sell',position_key=key,code=code),self.now)
