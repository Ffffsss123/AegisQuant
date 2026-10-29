import unittest,json
from types import SimpleNamespace as NS
from datetime import timedelta
import test_hk_transport as fixture
from trading.hk_cycle import run_once
from trading.hk_readonly import HKReadOnly
from trading.hk_reservations import Reservations

class HKCycleTests(unittest.TestCase):
    def setUp(self):
        fixture.HKTransportTests.setUp(self)
        self.sdk.Currency=NS(HKD='HKD')
        self.ctx.accinfo_query=lambda **kw:(0,[dict(currency='N/A',cash=1000000,total_assets=1000000,frozen_cash=0,market_val=0,interest_charged_amount=0)])
        self.ctx.position_list_query=lambda **kw:(0,[])
        self.reader=HKReadOnly(self.sdk,self.ctx,'8653',self.root/'raw.jsonl',lambda:self.now)
        self.res=Reservations(self.root/'res.sqlite','p')
        self.stock=dict(env='SIMULATE',account='us',hk_account='8653',portfolio_id='p',initial=500,loss_limit=100)
        stamp=self.now.isoformat()
        self.fx=dict(kind='fixed_simulation_accounting',env='SIMULATE',portfolio_id='p',account='8653',bound_at=stamp,hkd_per_usd=8,
            reference=dict(verified=True,source='fixture',as_of=stamp,hkd_per_usd=8))
        self.res.reserve(self.inputs['tag'],130,500)
        self.transport.submit(self.inputs,lambda r:True)
        self.row.update(dealt_qty=0,dealt_avg_price=0)
        self.now+=timedelta(seconds=61)
    def tearDown(self):self.res.close();fixture.HKTransportTests.tearDown(self)
    def cycle(self,books=None,fees=None):
        return run_once(self.reader,self.transport,self.res,self.stock,fx=self.fx,books=books or {},fee_records=fees or {})
    def test_overdue_cancel_precedes_failed_funds_query(self):
        def timeout(**kw):raise TimeoutError('funds unavailable')
        self.ctx.accinfo_query=timeout
        result=self.cycle()
        self.assertEqual([x[0] for x in self.calls],['place','cancel'])
        self.assertEqual(result['status'],'needs_attention')
        self.assertFalse(result['interface_acceptance'])
    def test_pending_orders_do_not_wait_for_quote_or_fee_provider(self):
        def unavailable():raise AssertionError('must not query providers while awaiting order')
        result=self.cycle(books=unavailable,fees=unavailable)
        self.assertEqual(result['status'],'waiting_orders')
        self.assertEqual([x[0] for x in self.calls],['place','cancel'])
    def test_complete_fill_reconciles_but_never_claims_acceptance(self):
        self.row.update(order_status='FILLED_ALL',dealt_qty=100,dealt_avg_price=10.02)
        self.ctx.position_list_query=lambda **kw:(0,[dict(code=self.inputs['code'],qty=100)])
        self.ctx.accinfo_query=lambda **kw:(0,[dict(currency='N/A',cash=998998,total_assets=1000000,frozen_cash=0,market_val=1002,interest_charged_amount=0)])
        stamp=self.now.isoformat();book=dict(bid=10,ask=10.02,bid_time=stamp,ask_time=stamp)
        result=self.cycle(books={self.inputs['code']:book},fees={'123':dict(source='broker',amount_hkd=20,evidence='fee query')})
        self.assertEqual(result['status'],'reconciled',result)
        self.assertFalse(result['interface_acceptance']);self.assertFalse(result['orders_enabled'])
        self.assertEqual([x[0] for x in self.calls],['place'])
    def test_read_only_unbound_cycle_does_not_write_risk_binding(self):
        self.row.update(order_status='CANCELLED_ALL')
        self.stock.pop('hk_account')
        result=self.cycle();self.assertEqual(result['status'],'awaiting_shared_binding')
        self.assertFalse((self.root/'data/trading/hk_risk_snapshot.json').exists())
    def test_proven_unsubmitted_hold_recovers_before_quote_provider(self):
        self.row.update(order_status='CANCELLED_ALL')
        tag='HK-SIM-aborted';self.res.reserve(tag,50,500)
        self.journal.db.execute('INSERT INTO aborted_admissions VALUES(?,?,?)',
            (tag,json.dumps(dict(account='8653',env='SIMULATE')),json.dumps(dict(broker_call_started=False))))
        def quotes():
            state=self.res.db.execute('SELECT state FROM holds WHERE key=?',(tag,)).fetchone()[0]
            self.assertEqual(state,'RELEASED')
            return {}
        result=self.cycle(books=quotes)
        self.assertEqual(result['recovered_unsubmitted'],[tag])
        self.assertEqual(result['status'],'reconciled',result)

    def test_opt_in_pending_loss_observation_runs_after_cancellation(self):
        def quotes():
            self.assertEqual([x[0] for x in self.calls],['place','cancel'])
            return {}
        result=run_once(self.reader,self.transport,self.res,self.stock,fx=self.fx,
            fee_records=None,books=quotes,observe_pending_loss=True)
        self.assertEqual(result['status'],'waiting_orders',result)
        self.assertTrue(result['loss_observation']['loss_observation_only'])
        self.assertFalse(result['orders_enabled'])
        self.assertEqual(float(self.res.committed_and_held()),130)
        self.assertFalse((self.root/'data/trading/hk_risk_snapshot.json').exists())

    def test_readonly_loss_collector_uses_complete_reader_without_mutation(self):
        from trading.hk_risk_producer import collect_loss
        self.ctx.history_order_list_query=self.ctx.order_list_query
        before=list(self.calls)
        row=collect_loss(self.reader,self.journal,self.stock,fx=self.fx,
            book_provider=lambda code:self.fail('No holdings require no quotes'),
            history_start=self.now.date().isoformat())
        self.assertTrue(row['loss_observation_only'])
        self.assertEqual(row['pending_order_ids'],['123'])
        self.assertEqual(self.calls,before)
        self.assertEqual(float(self.res.committed_and_held()),130)
