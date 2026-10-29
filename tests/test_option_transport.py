import unittest,tempfile,copy
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from tests import test_trading as t
from trading.option_transport import OptionStep,passed,fresh_offer,ready_for_reservation,settle,active
from trading.policy import Config,SafetyError
from trading.store import Store
from trading.quote_feed import QuoteUnavailable

class Broker(t.Fake):
    def __init__(self):super().__init__();self.time=t.NOW;self.missing=False
    def now(self):return self.time
    def book(self,code):
        if self.missing:raise QuoteUnavailable('waiting','waiting_quote')
        return dict(bid=.99,ask=1.,bid_time=self.time.isoformat(),ask_time=self.time.isoformat())
    def mark(self,code,now):return .99

class Transport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=Store(self.root/'data/trading/option_transport.sqlite')
        self.b=Broker();self.e=t.Engine(self.b,self.db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500,shared_capital=True,allocation=1))
        self.c=dict(code='US.OPT',underlying='US.A',right='CALL',expiry='2026-10-23',standard=True,multiplier=100,tick_size=.01)
        self.s=self.e.bind('chosen','SIMULATE',.6,t.NOW,extra=dict(purpose='option_transport_probe',probe_code='US.OPT',probe_phase='cancel_test',probe_contract=self.c,shared_capital=True))
        self.step=OptionStep(self.e,self.c)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def tick(self,seconds=0):self.b.time+=timedelta(seconds=seconds);return self.step.tick(self.s,self.b.time)
    def complete(self):
        self.tick();self.tick(61);self.tick();self.b.fill(1,1);self.tick();self.tick();self.b.fill(1,.99);self.tick()
    def test_actual_roundtrip_evidence(self):
        self.complete();self.assertTrue(passed(self.s));self.assertEqual(self.b.calls,3);self.assertEqual(self.s['cash'],497)
    def test_quote_wait_no_order_or_phase_claim(self):
        self.b.missing=True;self.assertEqual(self.tick(),'waiting_quote');self.assertEqual(self.b.calls,0);self.assertEqual(self.s['probe_phase'],'cancel_test')
    def test_submit_quote_failure_restores_phase(self):
        with patch.object(self.e,'submit',side_effect=QuoteUnavailable('waiting','waiting_quote')):
            with self.assertRaises(QuoteUnavailable):self.step.submit(self.s,'BUY',1,1,'cancel_wait',t.NOW)
        self.assertEqual(self.s['probe_phase'],'cancel_test')
    def test_restart_after_intent_no_duplicate(self):
        self.b.fail=True;self.tick();self.step=OptionStep(self.e,self.c);self.tick();self.assertEqual(self.b.calls,1);self.assertFalse(passed(self.s))
    def test_cancel_serviced_before_quote_wait(self):
        self.tick();self.b.missing=True;self.tick(61);self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL')
    def test_cancel_failure_stays_pending(self):
        self.tick();self.b.cancel_fail=True;self.assertEqual(self.tick(61),'needs_attention');self.assertFalse(passed(self.s));self.assertTrue(self.s['paused'])
        self.b.cancel_fail=False;self.tick();self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL');self.assertEqual(self.b.calls,1)
    def test_unexpected_fill_not_passed(self):
        self.tick();self.b.fill(1,.49)
        with self.assertRaises(SafetyError):self.tick()
        self.assertFalse(passed(self.s));self.assertEqual(self.b.calls,1)
    def test_budget_includes_both_fees(self):
        self.s['probe_phase']='buy';self.s['cash']=105
        with self.assertRaises(SafetyError):self.tick()
        self.assertEqual(self.b.calls,0)
    def test_manual_pause_preserved(self):
        self.e.controls=lambda:dict(paused=True);self.assertEqual(self.tick(),'needs_attention');self.assertEqual(self.b.calls,0)
    def test_missing_broker_id_never_passes(self):
        self.complete();next(iter(self.s['orders'].values()))['id']=None;self.assertFalse(passed(self.s))
    def test_real_never_passes(self):self.complete();self.s['env']='REAL';self.assertFalse(passed(self.s))
    def test_incomplete_sell_never_passes(self):
        self.complete();list(self.s['orders'].values())[-1]['booked_qty']=0;self.assertFalse(passed(self.s))
    def test_settlement_once_preserves_budget(self):
        self.complete();sd=Store(self.root/'stock.sqlite')
        try:
            stock=dict(account='chosen',env='SIMULATE',initial=500,cash=500);sd.save(stock,'test')
            settle(self.db,sd,t.NOW,self.root);settle(self.db,sd,t.NOW,self.root)
            self.assertEqual(sd.load()['cash'],497);self.assertEqual(sd.load()['initial'],500);self.assertEqual(len(sd.load()['capital_changes']),1);self.assertFalse(active(self.root))
        finally:sd.close()
    def test_independent_settlement_preserves_both_cash_ledgers(self):
        self.complete();sd=Store(self.root/'stock.sqlite')
        try:
            stock=dict(account='chosen',env='SIMULATE',initial=500,cash=410,budget_currency='USD',portfolio_id='p',capital_mode='independent_sleeves_v1',sleeve='US_STOCK')
            sd.save(stock,'test');self.s.update(capital_mode='independent_sleeves_v1',sleeve='US_OPTION',portfolio_id='p');self.db.save(self.s)
            settle(self.db,sd,t.NOW,self.root);settle(self.db,sd,t.NOW,self.root)
            self.assertEqual(sd.load(),stock)
            self.assertEqual(self.db.load()['cash'],497)
            self.assertEqual(self.db.load()['independent_settlement']['net_pnl_usd'],-3)
            stock['portfolio_id']='other';sd.save(stock)
            with self.assertRaises(SafetyError):settle(self.db,sd,t.NOW,self.root)
        finally:sd.close()
    def test_unknown_capital_mode_never_transfers_cash(self):
        self.complete();sd=Store(self.root/'stock.sqlite')
        try:
            stock=dict(account='chosen',env='SIMULATE',initial=500,cash=500)
            sd.save(stock)
            self.s['capital_mode']='independent_sleeves_v2';self.db.save(self.s)
            with self.assertRaises(SafetyError):settle(self.db,sd,t.NOW,self.root)
            self.assertEqual(sd.load(),stock)
            self.assertEqual(self.db.load()['cash'],497)
        finally:sd.close()
    def test_wrong_settlement_account(self):
        self.complete();sd=Store(self.root/'stock.sqlite')
        try:
            sd.save(dict(account='wrong',env='SIMULATE',initial=500,cash=500),'test')
            with self.assertRaises(SafetyError):settle(self.db,sd,t.NOW,self.root)
        finally:sd.close()
    def test_candidate_timestamp(self):
        self.assertTrue(fresh_offer(dict(created_at=t.NOW.isoformat()),t.NOW))
        self.assertFalse(fresh_offer(dict(created_at=t.NOW.isoformat()),t.NOW+timedelta(seconds=121)))
        self.assertFalse(fresh_offer(dict(created_at=t.NOW.isoformat()),t.NOW-timedelta(seconds=1)))
        self.assertFalse(fresh_offer(dict(created_at='invalid'),t.NOW))
    def test_reservation_rejects_live_and_pause(self):
        with self.assertRaises(SafetyError):ready_for_reservation(dict(env='REAL'),{}, {})
        from unittest.mock import patch
        with patch('trading.option_transport.available_cash') as shared_cash:
            with self.assertRaisesRegex(SafetyError,'legacy option reservation'):
                ready_for_reservation(dict(env='SIMULATE',capital_mode='independent_sleeves_v1'),{}, {})
            shared_cash.assert_not_called()
        with self.assertRaises(SafetyError):ready_for_reservation(dict(env='SIMULATE',purpose='strategy_simulation',paused=True),{}, {})
    def test_final_remote_evidence_required(self):
        from trading.option_transport import verify_flat_evidence
        self.complete();verify_flat_evidence(self.b,self.s)
        self.b.remote.pop()
        with self.assertRaises(SafetyError):verify_flat_evidence(self.b,self.s)
    def test_external_order_prevents_gate_release(self):
        from trading.option_transport import verify_flat_evidence
        self.complete();self.b.remote.append(dict(id='external',status='SUBMITTED'))
        with self.assertRaises(SafetyError):verify_flat_evidence(self.b,self.s)
    def test_exhausted_reads_still_service_cancel(self):
        self.tick();self.s['query_retry']=dict(exhausted=True);self.tick(61)
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL');self.assertEqual(self.b.calls,1)
    def test_revalidate_before_each_entry(self):
        self.step.validate_entry=lambda *args: (_ for _ in ()).throw(SafetyError('liquidity changed'))
        with self.assertRaises(SafetyError):self.tick()
        self.assertEqual(self.b.calls,0)

    def test_independent_completion_allows_verified_peer_holding_only(self):
        from trading.option_transport import verify_independent_evidence,verify_flat_evidence
        self.complete()
        self.s.update(capital_mode='independent_sleeves_v1',sleeve='US_OPTION',portfolio_id='p',shared_capital=False)
        self.db.save(self.s)
        stock_path=self.root/'data/trading/paper.sqlite';stock_db=Store(stock_path)
        try:
            stock=t.Engine(self.b,stock_db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind(
                'chosen','SIMULATE',.6,t.NOW,extra=dict(capital_mode='independent_sleeves_v1',sleeve='US_STOCK',portfolio_id='p',shared_capital=False))
            stock['positions']['US.A']=dict(qty=1,kind='STOCK',multiplier=1,cost=10,fees=0,realized=0)
            stock['cash']-=10;stock_db.save(stock);self.b.pos['US.A']=1
        finally:stock_db.close()
        paths={'US_STOCK':stock_path,'US_OPTION':self.db.path}
        archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
        result=verify_independent_evidence(self.root,self.b,paths,account='chosen',portfolio='p',archive=archive)
        self.assertTrue(result['option_transport_passed']);self.assertFalse(result['gate_released'])
        for _ in range(2):
            verify_independent_evidence(self.root,self.b,paths,account='chosen',portfolio='p',archive=archive,record_completion=True)
        self.assertEqual(self.db.load()['independent_settlement']['net_pnl_usd'],-3)
        self.assertFalse((self.root/'data/trading/option_transport_gate.json').exists())
        sd=Store(stock_path)
        try:self.assertEqual(sd.load()['cash'],490)
        finally:sd.close()
        with self.assertRaises(SafetyError):verify_flat_evidence(self.b,self.s)
        self.b.pos['US.EXTERNAL']=1
        with self.assertRaises(SafetyError):
            verify_independent_evidence(self.root,self.b,paths,account='chosen',portfolio='p',archive=archive)
        self.assertEqual(self.b.calls,3)

class IndependentRunner(unittest.TestCase):
    def test_existing_independent_ledger_bypasses_contract_and_legacy_engine(self):
        from unittest.mock import MagicMock
        from trading import option_transport as module
        class EndIteration(BaseException):pass
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'data/trading/option_transport.sqlite'
            path.parent.mkdir(parents=True);db=Store(path)
            db.save(dict(env='SIMULATE',account='chosen',capital_mode='independent_sleeves_v1'))
            db.close();broker=MagicMock()
            with patch.object(module,'ROOT',root), patch.object(module,'protection_passed',return_value=True), patch.object(module,'OptionBroker',return_value=broker), patch.object(module,'independent_execution_cycle',return_value={'status':'no_entry'}) as joint, patch.object(module,'OptionStep') as legacy, patch.object(module,'report'), patch.object(module.time,'sleep',side_effect=EndIteration):
                with self.assertRaises(EndIteration):module.main()
                joint.assert_called_once();legacy.assert_not_called();broker.close.assert_called_once()

    def test_independent_error_uses_joint_maintenance_outside_ledger_lock(self):
        from unittest.mock import MagicMock
        from trading import option_transport as module
        class EndIteration(BaseException):pass
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'data/trading/option_transport.sqlite'
            path.parent.mkdir(parents=True);db=Store(path)
            db.save(dict(env='SIMULATE',account='chosen',capital_mode='independent_sleeves_v1',alerts=[]))
            db.close();broker=MagicMock()
            with patch.object(module,'ROOT',root), patch.object(module,'protection_passed',return_value=True), patch.object(module,'OptionBroker',return_value=broker), patch.object(module,'independent_execution_cycle',side_effect=SafetyError('injected')), patch.object(module,'independent_maintenance') as joint, patch.object(module,'Engine') as engine, patch.object(module,'OptionStep') as legacy, patch.object(module,'report'), patch.object(module.time,'sleep',side_effect=EndIteration):
                with self.assertRaises(EndIteration):module.main()
                self.assertEqual(joint.call_count,1);legacy.assert_not_called();engine.return_value.manage_orders.assert_not_called();broker.close.assert_called_once()

    def test_independent_maintenance_checks_identity_before_joint_dispatch(self):
        from trading.option_transport import independent_maintenance
        stock=dict(env='SIMULATE',account='chosen',capital_mode='independent_sleeves_v1')
        with patch('trading.option_transport.ledger',return_value=stock), patch('trading.paper_strategy.independent_order_maintenance') as joint:
            independent_maintenance(Path('/unused'),object(),stock)
            joint.assert_called_once();joint.reset_mock()
            with self.assertRaises(SafetyError):independent_maintenance(Path('/unused'),object(),dict(stock,account='other'))
            joint.assert_not_called()

class ExplicitContractValidation(unittest.TestCase):
    def test_isolated_scan_and_cash_do_not_read_production(self):
        import json
        from unittest.mock import MagicMock
        from trading import option_transport as module
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'data/trading/market/scan.json';path.parent.mkdir(parents=True)
            scan=dict(complete=True,date='2026-09-04',signals={'US.TEST':{'fixture':True}})
            path.write_text(json.dumps(scan));b=MagicMock();b.now.return_value=t.NOW
            b.execution_session.return_value=('2026-09-08','2026-09-04')
            b.option_chain.return_value=[dict(code='US.OPTION')];b.snapshots.return_value=[dict(price_spread=.01)]
            b.book.return_value={'fixture':'book'}
            state=dict(probe_contract=dict(code='US.OPTION',underlying='US.TEST'),cash=500)
            with patch.object(module,'normalize_contract',return_value=dict(code='US.OPTION')),patch.object(module,'option_candidates',return_value=[{}]) as candidates:
                result=module.validate_entry(b,state,t.NOW,root=root)
                self.assertEqual(result['book'],b.book.return_value)
                self.assertEqual(candidates.call_args.args[1],scan['signals'])
                self.assertEqual(candidates.call_args.args[-2:],(495,5))
                scan['complete']=False
                with self.assertRaisesRegex(SafetyError,'complete scan'):
                    module.validate_contract(b,state['probe_contract'],t.NOW,scan=scan,available_cash=500)
                self.assertEqual(candidates.call_count,1)

    def test_invalid_sleeve_cash_fails_before_market_queries(self):
        from unittest.mock import MagicMock
        from trading.option_transport import validate_contract
        for cash in (float('nan'),float('inf'),-1,True,None,10):
            with self.subTest(cash=cash):
                broker=MagicMock()
                with self.assertRaises(SafetyError):
                    validate_contract(broker,{},t.NOW,scan={},available_cash=cash)
                self.assertEqual(broker.mock_calls,[])

class IndependentContractPreparation(unittest.TestCase):
    def test_contract_cost_is_bounded_by_admitted_request(self):
        from contextlib import contextmanager
        from decimal import Decimal
        from unittest.mock import MagicMock
        from trading.option_transport import prepare_independent_contract
        engine=MagicMock();engine.news.return_value=dict(multiplier=1,global_block=False);engine.store.load.return_value={'orders':{}};engine.controls.return_value=dict(paused=False,stopped=False,reset_pending=False);active=[]
        @contextmanager
        def admitted(*args,**kwargs):
            self.assertEqual(kwargs['sleeve'],'US_OPTION');active.append(True)
            try:yield {},dict(available_usd=Decimal('500'),required_usd=Decimal('120')),{'US_OPTION':engine}
            finally:active.pop()
        def validate(*args,**kwargs):
            self.assertTrue(active);self.assertEqual(kwargs['available_cash'],Decimal('120'))
            return dict(code='US.OPTION',underlying='US.TEST')
        with patch('trading.simulation_admission.admitted_us_session',admitted),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract',side_effect=validate):
            result=prepare_independent_contract(Path('/isolated'),MagicMock(),{},account='a',hk_account='h',portfolio='p',archive={},risk_path='unused',contract={'underlying':'US.TEST'},required_usd=120)
        self.assertFalse(result['submission_enabled']);self.assertFalse(active)
        self.assertEqual(engine.store.event.call_args.args[0],'independent_option_contract_prepared')
        engine.store.event.reset_mock()
        with patch('trading.simulation_admission.admitted_us_session',admitted),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract',side_effect=SafetyError('option quote stale')):
            with self.assertRaisesRegex(SafetyError,'option quote stale'):
                prepare_independent_contract(Path('/isolated'),MagicMock(),{},account='a',hk_account='h',portfolio='p',archive={},risk_path='unused',contract={'code':'US.OPTION','underlying':'US.TEST'},required_usd=120)
        event,body=engine.store.event.call_args.args
        self.assertEqual(event,'independent_option_contract_rejected')
        self.assertEqual(body['reason'],'option quote stale')
        self.assertFalse(body['submission_enabled']);self.assertFalse(active)
        engine.controls.return_value=dict(paused=True,stopped=False,reset_pending=False)
        with patch('trading.simulation_admission.admitted_us_session',admitted),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract',side_effect=validate):
            with self.assertRaisesRegex(SafetyError,'controls changed'):
                prepare_independent_contract(Path('/isolated'),MagicMock(),{},account='a',hk_account='h',portfolio='p',archive={},risk_path='unused',contract={'underlying':'US.TEST'},required_usd=120)
        self.assertEqual(engine.store.event.call_args.args[0],'independent_option_contract_rejected')
        engine.controls.return_value=dict(paused=False,stopped=False,reset_pending=False,require_news_opportunity=True)
        engine.news.return_value=dict(multiplier=0,global_block=False,entry_evidence=[{'fixture':'positive'}])
        with patch('trading.simulation_admission.admitted_us_session',admitted),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract') as market:
            with self.assertRaisesRegex(SafetyError,'News risk filter'):
                prepare_independent_contract(Path('/isolated'),MagicMock(),{},account='a',hk_account='h',portfolio='p',archive={},risk_path='unused',contract={'underlying':'US.TEST'},required_usd=120)
            market.assert_not_called()
        engine.news.return_value=dict(multiplier=1,global_block=False,entry_evidence=[])
        with patch('trading.simulation_admission.admitted_us_session',admitted),patch('trading.option_transport.read_json',return_value={}),patch('trading.option_transport.validate_contract',side_effect=validate):
            with self.assertRaisesRegex(SafetyError,'positive news opportunity'):
                prepare_independent_contract(Path('/isolated'),MagicMock(),{},account='a',hk_account='h',portfolio='p',archive={},risk_path='unused',contract={'underlying':'US.TEST'},required_usd=120)

class ConfirmedExitAcknowledgement(unittest.TestCase):
    def test_uncertain_transport_is_not_reported_as_acknowledged(self):
        from unittest.mock import MagicMock
        from trading.option_transport import submit_confirmed_option_exit,OptionStep
        state=dict(env='SIMULATE',purpose='option_transport_probe',probe_phase='sell',
                   probe_code='US.O',probe_contract={'code':'US.O'},positions={'US.O':{'qty':1}})
        engine=MagicMock();engine.blocked_orders.return_value=False
        engine.store.load.side_effect=[state,{'orders':{'tag':{'status':'UNKNOWN','id':None}}}]
        with patch('trading.option_transport.quote_price',return_value=1), patch.object(OptionStep,'submit',return_value='tag'):
            result=submit_confirmed_option_exit(engine,t.NOW)
        self.assertFalse(result['acknowledged'])
        self.assertEqual(result['order_status'],'UNKNOWN')

class ConfirmedExitCycle(unittest.TestCase):
    def test_cancellation_precedes_new_session_and_unconfirmed_phase_cannot_send(self):
        from contextlib import contextmanager
        from unittest.mock import MagicMock
        from trading.option_transport import confirmed_option_exit_cycle
        calls=[];engine=MagicMock();engine.store.load.return_value={'probe_phase':'sell_wait'}
        @contextmanager
        def session(*args,**kwargs):
            calls.append('session');self.assertEqual(kwargs['option_exit_owner'],'US_OPTION')
            yield {},{'fresh':True},{'US_OPTION':engine}
        def cancel(*args,**kwargs):
            calls.append('cancel');self.assertTrue(kwargs['honor_controls']);return {'done':True}
        with patch('trading.simulation_reconcile.cancel_expired_locked',side_effect=cancel), patch('trading.simulation_reconcile.account_session',side_effect=session), patch('trading.option_transport.submit_confirmed_option_exit') as submit:
            result=confirmed_option_exit_cycle(Path('/unused'),object(),{},account='chosen',archive={})
        self.assertEqual(calls,['cancel','session']);submit.assert_not_called()
        self.assertEqual(result['status'],'maintenance_only')

class HKRefreshBeforeAdmission(unittest.TestCase):
    def test_failed_refresh_never_enters_us_session_or_uses_old_file(self):
        from trading.simulation_admission import admitted_us_session
        with patch('trading.simulation_reconcile.account_session') as session:
            with self.assertRaisesRegex(SafetyError,'HK risk refresh did not reconcile'):
                with admitted_us_session(Path('/unused'),object(),{'US_STOCK':'s','US_OPTION':'o'},
                        account='us',hk_account='hk',portfolio='p',archive={},risk_path='risk',
                        sleeve='US_OPTION',required_usd=100,
                        refresh_hk=lambda:{'status':'needs_attention'}):
                    self.fail('Admission yielded after failed refresh')
            session.assert_not_called()

class HKRefreshInvalidation(unittest.TestCase):
    def test_history_failure_removes_previous_success_before_cycle(self):
        from types import SimpleNamespace
        from trading.hk_maintenance import refresh_bound_risk
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'data/trading/hk_risk_snapshot.json'
            path.parent.mkdir(parents=True);path.write_text('{"account":"hk","reconciled":true}')
            reader=SimpleNamespace(account='hk',clock=lambda:t.NOW)
            with patch('trading.hk_maintenance.strategy',return_value={'hk_account':'hk'}), patch('trading.simulation_history.bound_hk_history_start',side_effect=SafetyError('Bad history')), patch('trading.hk_maintenance.run_once') as cycle:
                with self.assertRaisesRegex(SafetyError,'Bad history'):
                    refresh_bound_risk(root,reader,SimpleNamespace(root=root),object(),books={})
                cycle.assert_not_called()
            self.assertFalse(path.exists())

class MigrationEntryInterlock(unittest.TestCase):
    def test_pending_or_malformed_migration_blocks_legacy_reservation(self):
        from trading.option_transport import ready_for_reservation
        for value in (True,None,0,'false',{}):
            with self.subTest(value=value),self.assertRaisesRegex(SafetyError,'Capital migration pending'):
                ready_for_reservation({}, {}, {'capital_migration_pending':value})
