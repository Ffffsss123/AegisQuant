import unittest,json
from datetime import timedelta
import test_hk_reconciliation as fixture
from trading.hk_reservations import Reservations
from trading.hk_risk_producer import publish
from trading.hk_shared_admission import exposure
from trading.hk_fx import validate_accounting_binding
from trading.policy import SafetyError
from pathlib import Path

class HKRiskProducerTests(unittest.TestCase):
    def setUp(self):
        fixture.HKInventoryTests.setUp(self)
        self.root=Path(self.tmp.name);self.res=Reservations(self.root/'reserved.sqlite','p')
        self.res.reserve('HK-SIM-buy',130,500)
        self.path=self.root/'data/trading/hk_risk_snapshot.json'
        self.stock=dict(account='us',hk_account='8653',env='SIMULATE',portfolio_id='p',initial=500,loss_limit=100)
        self.fees={'1':dict(source='broker',amount_hkd=20,evidence='query')}
        stamp=self.now.isoformat()
        self.fx=dict(kind='fixed_simulation_accounting',env='SIMULATE',portfolio_id='p',account='8653',
            bound_at=stamp,hkd_per_usd=8,reference=dict(verified=True,source='fixture',as_of=stamp,hkd_per_usd=8))
        self.books={'HK.00001':dict(bid=11,ask=11.01,bid_time=stamp,ask_time=stamp)}
    def tearDown(self):self.res.close();fixture.HKInventoryTests.tearDown(self)
    def publish(self):
        return publish(self.path,stock=self.stock,accounts=self.accounts,snapshot=self.snapshot,
            submissions=self.journal,reservations=self.res,fee_records=self.fees,books=self.books,fx=self.fx,now=self.now)
    def test_bound_loss_requires_existing_configuration_and_closes_journal(self):
        from types import SimpleNamespace
        from unittest.mock import patch,Mock
        from trading.hk_risk_producer import collect_bound_loss
        reader=SimpleNamespace(account='8653',clock=lambda:self.now)
        base=self.root/'data/trading';base.mkdir(parents=True,exist_ok=True)
        with self.assertRaises(FileNotFoundError):
            collect_bound_loss(self.root,reader,self.stock,book_provider=self.books.get,history_start='2026-01-01')
        self.assertFalse((base/'hk_submissions.sqlite').exists())
        (base/'hk_fx_binding.json').write_text(json.dumps(self.fx))
        journal=Mock()
        with patch('trading.hk_submission.SubmissionJournal.open_existing_readonly',return_value=journal), patch('trading.hk_risk_producer.collect_loss',side_effect=SafetyError('snapshot failed')):
            with self.assertRaisesRegex(SafetyError,'snapshot failed'):
                collect_bound_loss(self.root,reader,self.stock,book_provider=self.books.get,history_start='2026-01-01')
        journal.close.assert_called_once_with()

    def test_filled_inventory_publishes_exact_fee_count_once(self):
        row=self.publish();self.assertTrue(row['reconciled']);self.assertFalse(row['entry_block'])
        hk=exposure(self.stock,self.now,self.root)
        from trading.hk_fees import order_upper_bound
        exit_fee=float(order_upper_bound(1100,100)['total_hkd'])/8
        debit=(999+20)/8
        self.assertAlmostEqual(hk['occupied_usd'],debit+exit_fee)
        self.assertAlmostEqual(hk['total_net_pnl_usd'],137.5-debit-exit_fee)
        again=self.publish();self.assertEqual(row['occupied_usd'],again['occupied_usd'])
    def test_failed_quote_removes_prior_success_snapshot(self):
        self.publish();self.books['HK.00001']['bid_time']=(self.now-timedelta(seconds=31)).isoformat()
        with self.assertRaises(SafetyError):self.publish()
        self.assertFalse(self.path.exists())
    def test_pending_order_never_publishes_zero_unrealized_as_success(self):
        self.row.update(dealt_qty=20,order_status='FILLED_PART');self.snapshot['positions']['response'][0]['qty']=20
        with self.assertRaises(SafetyError):self.publish()
        self.assertFalse(self.path.exists());self.assertEqual(float(self.res.committed_and_held()),130)
    def test_fixed_reference_validates_at_activation_not_repriced_daily(self):
        later=self.now+timedelta(days=10)
        self.assertEqual(validate_accounting_binding(self.fx,'p','8653',later),8)
        self.fx['hkd_per_usd']=7.8
        with self.assertRaises(SafetyError):validate_accounting_binding(self.fx,'p','8653',later)
    def test_partial_exit_keeps_snapshot_unavailable(self):
        self.publish()
        sell=dict(self.request,side='SELL',tag='HK-SIM-sell',position_key='HK-SIM-buy',qty=40,price=11)
        self.journal.claim(sell,self.now)
        self.snapshot['orders']['response'].append(dict(self.row,remark='HK-SIM-sell',order_id='2',trd_side='SELL',qty=40,price=11,dealt_qty=40,dealt_avg_price=11))
        self.snapshot['positions']['response'][0]['qty']=60
        self.fees['2']=dict(source='broker',amount_hkd=20,evidence='sell fee')
        with self.assertRaises(SafetyError):self.publish()
        self.assertFalse(self.path.exists())

    def test_partial_fill_loss_observation_preserves_reservation(self):
        from trading.hk_risk_producer import observe_loss
        from trading.hk_fees import order_upper_bound
        self.row.update(dealt_qty=20,order_status='FILLED_PART')
        self.snapshot['positions']['response'][0]['qty']=20
        before=self.res.db.total_changes
        row=observe_loss(stock=self.stock,accounts=self.accounts,snapshot=self.snapshot,
            submissions=self.journal,fee_records=self.fees,books=self.books,fx=self.fx,now=self.now)
        exit_fee=float(order_upper_bound(220,20)['total_hkd'])
        pending=max(0,float(order_upper_bound(self.row['qty']*self.row['price'],self.row['qty'])['total_hkd'])-20)
        self.assertAlmostEqual(float(row['equity_usd']),500+(220-20*self.row['dealt_avg_price']-20-exit_fee-pending)/8)
        self.assertEqual(row['pending_order_ids'],['1'])
        modeled=observe_loss(stock=self.stock,accounts=self.accounts,snapshot=self.snapshot,
            submissions=self.journal,fee_records=None,books=self.books,fx=self.fx,now=self.now)
        self.assertTrue(modeled['loss_observation_only'])
        self.assertFalse(modeled['orders_enabled'])

        from trading.simulation_admission import observe_us_loss
        from trading.simulation_loss_latch import initialize
        risk=self.root/'loss.sqlite';initialize(risk,'p',inherited_stop=False)
        states={name:dict(env='SIMULATE',account='us',initial=500,cash=350 if name=='US_STOCK' else 500,
            budget_currency='USD',positions={},orders={},capital_mode='independent_sleeves_v1',
            sleeve=name,portfolio_id='p',shared_capital=False) for name in ('US_STOCK','US_OPTION')}
        result=observe_us_loss(states=states,books={},hk_snapshot=None,hk_loss_observation=row,
            account='us',hk_account='8653',portfolio='p',now=self.now,risk_path=risk)
        self.assertTrue(result['loss_latched'])
        self.assertTrue(result['entry_block'])
        self.assertTrue(all(value==0 for value in result['available'].values()))

        self.assertEqual(self.res.db.total_changes,before)
        self.assertEqual(float(self.res.committed_and_held()),130)
        self.assertFalse(self.path.exists())
