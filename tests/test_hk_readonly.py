import tempfile,unittest,json
from pathlib import Path
from types import SimpleNamespace as NS
from datetime import datetime,timezone,timedelta
from trading.hk_readonly import HKReadOnly
from trading.policy import SafetyError

class HKReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'raw.jsonl'
        self.now=datetime.now(timezone.utc);self.calls=[]
        self.account=dict(acc_id=8653,trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')
        def query(name,rows):
            def run(**kw):self.calls.append((name,kw));return 0,rows
            return run
        self.ctx=NS(get_acc_list=query('accounts',[self.account]),accinfo_query=query('funds',[dict(currency='N/A',cash=1000000,total_assets=1000000,frozen_cash=0,market_val=0,interest_charged_amount=0)]),position_list_query=query('positions',[]),order_list_query=query('orders',[]))
        self.sdk=NS(RET_OK=0,TrdEnv=NS(SIMULATE='SIMULATE'),Currency=NS(HKD='HKD'))
        self.reader=HKReadOnly(self.sdk,self.ctx,'8653',self.path,lambda:self.now)
    def tearDown(self):self.tmp.cleanup()
    def test_reads_explicit_account_and_preserves_raw_evidence(self):
        _,snapshot=self.reader.flat_snapshot()
        self.assertEqual(snapshot['orders_submitted'],0)
        self.assertEqual(len(self.path.read_text().splitlines()),4)
        for name,kw in self.calls[1:]:
            self.assertEqual(kw['acc_id'],8653);self.assertEqual(kw['trd_env'],'SIMULATE');self.assertTrue(kw['refresh_cache'])
        self.assertEqual(next(kw for name,kw in self.calls if name=='funds')['currency'],'HKD')
    def test_real_account_stops_before_funds(self):
        self.account['trd_env']='REAL'
        with self.assertRaises(SafetyError):self.reader.flat_snapshot()
        self.assertEqual(len(self.calls),1)
    def test_failure_preserved_and_no_following_queries(self):
        self.ctx.accinfo_query=lambda **kw:(-1,'network timeout')
        with self.assertRaises(SafetyError):self.reader.flat_snapshot()
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(rows[-1]['return_code'],-1);self.assertEqual(len(rows),3)
    def test_slow_sequence_not_relabelled_fresh(self):
        def slow(**kw):self.now+=timedelta(seconds=31);return 0,[]
        self.ctx.order_list_query=slow
        with self.assertRaises(SafetyError):self.reader.flat_snapshot()

    def test_current_order_overrides_older_history_status(self):
        from trading.hk_policy import HK
        self.ctx.history_order_list_query=lambda **kw:(0,[dict(order_id='1',order_status='SUBMITTED')])
        self.ctx.order_list_query=lambda **kw:(0,[dict(order_id='1',order_status='FILLED_ALL')])
        _,snapshot=self.reader.snapshot(history_start=self.now.astimezone(HK).date().isoformat())
        self.assertEqual(snapshot['orders']['response'],[dict(order_id='1',order_status='FILLED_ALL')])
        self.assertEqual(len(self.path.read_text().splitlines()),5)
        history=next(json.loads(line) for line in self.path.read_text().splitlines() if json.loads(line)['query']=='history_orders')
        self.assertEqual(history['request'],dict(acc_id=8653,trd_env='SIMULATE',start=self.now.astimezone(HK).date().isoformat(),end=self.now.astimezone(HK).date().isoformat()))
    def test_duplicate_rows_not_silently_dropped_during_merge(self):
        from trading.hk_policy import HK
        self.ctx.history_order_list_query=lambda **kw:(0,[dict(order_id='1'),dict(order_id='1')])
        with self.assertRaises(SafetyError):
            self.reader.snapshot(history_start=self.now.astimezone(HK).date().isoformat())

    def test_order_service_is_available_before_funds_timeout(self):
        def timeout(**kw):raise TimeoutError()
        self.ctx.accinfo_query=timeout
        accounts,orders=self.reader.orders_snapshot()
        self.assertEqual([name for name,_ in self.calls],['accounts','orders'])
        self.assertEqual(orders['orders']['return_code'],0)
        self.assertNotIn('funds',orders)
        with self.assertRaises(TimeoutError):self.reader.complete_snapshot(accounts,orders)
        raw=[json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(raw[-1]['error_type'],'TimeoutError')
        self.assertIsNone(raw[-1]['return_code'])

    def test_migration_reads_complete_bound_history_and_rejects_old_order(self):
        from trading.hk_policy import HK
        state=dict(env='SIMULATE',hk_account='8653',
                   hk_history_start=self.now.astimezone(HK).date().isoformat())
        self.ctx.history_order_list_query=lambda **kw:(0,[])
        _,snapshot=self.reader.migration_flat_snapshot(state)
        self.assertEqual(snapshot['orders']['response'],[])
        rows=[json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual([r['query'] for r in rows],
                         ['accounts','history_orders','orders','funds','positions'])
        self.ctx.history_order_list_query=lambda **kw:(0,[dict(order_id='old',order_status='CANCELLED_ALL')])
        with self.assertRaises(SafetyError):self.reader.migration_flat_snapshot(state)
        state.pop('hk_history_start')
        count=len(self.path.read_text().splitlines())
        with self.assertRaises(SafetyError):self.reader.migration_flat_snapshot(state)
        self.assertEqual(len(self.path.read_text().splitlines()),count)
