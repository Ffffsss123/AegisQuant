import unittest
from types import SimpleNamespace as NS
from datetime import timedelta
import test_hk_reconciliation as fixture
from pathlib import Path
from trading.hk_lifecycle import service_orders
from trading.policy import SafetyError

class HKLifecycleTests(unittest.TestCase):
    setUp=fixture.HKInventoryTests.setUp
    tearDown=fixture.HKInventoryTests.tearDown
    def service(self,cancel=None):
        self.journal.db.execute('CREATE TABLE IF NOT EXISTS cancels (tag TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)')
        self.cancelled=[]
        def normal(tag):self.cancelled.append(tag);return 'cancel_requested'
        transport=NS(journal=self.journal,root=Path(self.tmp.name),cancel=cancel or normal)
        return service_orders(self.journal,transport,self.accounts,self.snapshot,self.now)
    def pending(self):
        self.now+=timedelta(seconds=61);self.snapshot['checked_at']=self.now.isoformat()
        self.row.update(dealt_qty=0,dealt_avg_price=0,order_status='SUBMITTED');self.snapshot['positions']['response']=[]
    def test_unknown_order_does_not_prevent_own_overdue_cancel(self):
        self.pending();self.snapshot['orders']['response'].append(dict(self.row,remark='manual',order_id='2'))
        result=self.service();self.assertEqual(self.cancelled,['HK-SIM-buy'])
        self.assertTrue(result['entry_block']);self.assertTrue(result['errors'])
        self.assertEqual(self.journal.db.execute('SELECT order_id FROM submissions').fetchone()[0],'1')
    def test_before_deadline_never_cancels(self):
        self.row.update(dealt_qty=0,dealt_avg_price=0,order_status='SUBMITTED');self.snapshot['positions']['response']=[]
        result=self.service();self.assertFalse(self.cancelled)
        self.assertEqual(result['actions'][0]['status'],'pending_before_deadline')
    def test_missing_submission_stays_unresolved_no_resend(self):
        self.pending();self.snapshot['orders']['response']=[]
        result=self.service();self.assertFalse(self.cancelled);self.assertTrue(result['entry_block'])
    def test_cancel_error_retained_and_other_service_results_survive(self):
        self.pending()
        def fail(tag):raise TimeoutError('uncertain cancellation')
        result=self.service(cancel=fail);self.assertTrue(result['errors']);self.assertTrue(result['entry_block'])
    def test_existing_cancel_claim_is_only_polled(self):
        self.pending();self.service()
        self.journal.db.execute('INSERT INTO cancels VALUES(?,?)',('HK-SIM-buy',self.now.isoformat()))
        result=self.service();self.assertFalse(self.cancelled)
        self.assertEqual(result['actions'][0]['status'],'awaiting_cancel_confirmation')
    def test_wrong_account_never_touches_orders(self):
        self.snapshot['account']='7929'
        with self.assertRaises(SafetyError):self.service()
