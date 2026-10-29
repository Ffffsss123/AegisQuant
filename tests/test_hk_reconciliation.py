import unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone
from trading.hk_submission import SubmissionJournal
from trading.hk_reconciliation import inventory
from trading.policy import SafetyError

class HKInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.journal=SubmissionJournal(Path(self.tmp.name)/'sub.sqlite','8653')
        self.now=datetime.now(timezone.utc)
        self.accounts=[dict(acc_id='8653',trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'])]
        self.request=dict(account='8653',env='SIMULATE',market='HK',currency='HKD',side='BUY',
            order_type='NORMAL',time_in_force='DAY',tag='HK-SIM-buy',code='HK.00001',qty=100,price=10)
        self.journal.claim(self.request,self.now)
        self.row=dict(remark='HK-SIM-buy',order_id='1',code='HK.00001',trd_side='BUY',qty=100,price=10,
                      dealt_qty=100,dealt_avg_price=9.99,order_status='FILLED_ALL')
        self.snapshot=dict(account='8653',env='SIMULATE',checked_at=self.now.isoformat(),
            orders=dict(return_code=0,response=[self.row]),positions=dict(return_code=0,response=[dict(code='HK.00001',qty=100)]))
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def run_check(self):return inventory(self.journal,self.accounts,self.snapshot,self.now)
    def test_full_fill_and_partial_fill_quantities_not_order_quantity(self):
        self.assertEqual(self.run_check()['positions'],{'HK.00001':'100'})
        self.row.update(dealt_qty=20,order_status='FILLED_PART');self.snapshot['positions']['response'][0]['qty']=20
        result=self.run_check();self.assertTrue(result['pending_or_unknown']);self.assertFalse(result['fees_reconciled'])
        self.row['order_status']='CANCELLED_PART'
        self.assertFalse(self.run_check()['pending_or_unknown'])
    def test_manual_orders_positions_and_missing_history_rejected(self):
        self.snapshot['orders']['response'].append(dict(self.row,remark='manual',order_id='2'))
        with self.assertRaises(SafetyError):self.run_check()
        self.snapshot['orders']['response']=[self.row]
        self.snapshot['positions']['response'].append(dict(code='HK.00002',qty=100))
        with self.assertRaises(SafetyError):self.run_check()
        self.snapshot['positions']['response'].pop();self.snapshot['orders']['response']=[]
        with self.assertRaises(SafetyError):self.run_check()
    def test_cancel_race_invalid_status_and_limit_violation_rejected(self):
        self.row['order_status']='CANCELLED_ALL'
        with self.assertRaises(SafetyError):self.run_check()
        self.row['order_status']='FILLED_ALL';self.row['dealt_avg_price']=10.1
        with self.assertRaises(SafetyError):self.run_check()
    def test_actual_sell_fill_reduces_inventory_to_flat(self):
        sell=dict(self.request,side='SELL',tag='HK-SIM-sell',price=11,position_key='HK-SIM-buy')
        self.journal.claim(sell,self.now)
        self.snapshot['orders']['response'].append(dict(self.row,remark=sell['tag'],order_id='2',trd_side='SELL',price=11,dealt_avg_price=11))
        self.snapshot['positions']['response']=[]
        result=self.run_check();self.assertEqual(result['positions'],{});self.assertFalse(result['fees_reconciled'])
    def test_wrong_account_or_duplicate_broker_identity_rejected(self):
        self.snapshot['account']='7929'
        with self.assertRaises(SafetyError):self.run_check()
        self.snapshot['account']='8653';self.snapshot['orders']['response'].append(dict(self.row))
        with self.assertRaises(SafetyError):self.run_check()
