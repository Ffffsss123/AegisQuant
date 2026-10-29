import json,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from trading.live_trading import (Portfolio,private_write,execute,public_status,pause_local,
    config,CONFIRM,simulation_evidence,portfolio_lock)
from trading.policy import SafetyError
from tests.test_trading import Fake


class LiveFake(Fake):
    def __init__(self,market,account,*_):
        super().__init__();self.market=market;self.account=account;self.env='REAL'
        from trading.hk_policy import HK
        from trading.moomoo import NY
        self.timezone=NY if market=='US' else HK
    def bind(self,account,env):
        if account!=self.account or env!='REAL':raise SafetyError('Wrong live account')
    def now(self):return datetime.now(timezone.utc)
    def book(self,code):
        p=10 if self.market=='US' else 2
        return dict(bid=p,ask=p,bid_time=self.now().isoformat(),ask_time=self.now().isoformat())
    def execution_session(self,now,strict=True):
        day=now.astimezone(self.timezone).date()
        return day.isoformat(),(day-timedelta(days=1)).isoformat()
    def calendar(self,now):
        return [dict(time=d,trade_date_type='WHOLE') for d in reversed(self.execution_session(now))]
    def completed_session(self,now):return self.execution_session(now)[1]
    def stock_metadata(self,code,qty,price):
        lot=1 if self.market=='US' else 100
        if not code.startswith(self.market+'.') or qty%lot:raise SafetyError('Wrong identity or board lot')
        return dict(lot=lot,snapshot={},checked_at=self.now().isoformat())
    def close(self):pass


class LivePortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.base=self.root/'data/trading/live'
        self.c=dict(version=1,env='REAL',port=11111,firm='FUTUAU',reserve_usd=390,max_positions=2,
            joint_loss_limit_usd=50,automatic_entries=False,markets={
                'US':dict(account='111',budget_native=200,loss_native=40,fee_reserve_native=3),
                'HK':dict(account='222',budget_native=1560,loss_native=200,fee_reserve_native=150)})
        private_write(self.base/'config.json',self.c)
        private_write(self.base/'fx.json',dict(verified=True,source='https://reference.example',
            as_of=datetime.now(timezone.utc).isoformat(),hkd_per_usd='7.8'))
        self.b={}
        def factory(m,*a):self.b[m]=LiveFake(m,*a);return self.b[m]
        self.factory=factory
        self.evidence=patch('trading.live_trading.simulation_evidence',return_value={'accepted':True});self.evidence.start()
        self.worker=patch('trading.live_trading.require_worker');self.worker.start()
        self.regression=patch('trading.validation.protection_passed',return_value=True);self.regression.start()
        self.p=Portfolio(self.root,factory);self.p.bind();self.p.enable(CONFIRM)
        for m,db in self.p.stores.items():
            s=db.load();s['stocks_enabled_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat();db.save(s)
        market=self.root/'data/trading/market';day=self.b['US'].completed_session(self.b['US'].now())
        private_write(market/('catalog_'+day+'.json'),dict(directory=[{
            'Symbol':'A','Security Name':'A Common Stock','ETF':'N','Test Issue':'N','_exchange':'NYSE'}],
            basic={'US.A':dict(suspension=False,delisting=False,lot_size=1)}))
        private_write(market/'scan.json',dict(complete=True,date=day,signals={'US.A':dict(buy=True,liquid=True)}))
        hk=self.root/'data/trading/hk_market';private_write(hk/'catalog.json',{})
        private_write(hk/'scan.json',dict(complete=True,date=self.b['HK'].completed_session(self.b['HK'].now()),
            signals={'HK.00001':dict(buy=True,liquid=True)}))
        for e in self.p.engines.values():e.news=lambda *_:dict(multiplier=1,reasons=[],event_ids=[])
    def tearDown(self):self.p.close();self.evidence.stop();self.regression.stop();self.worker.stop();self.tmp.cleanup()
    def ticket(self,market='US',code='US.A',side='BUY',qty=1,price=10,key='a'*32):
        return self.p.order(market,code,side,qty,price,'PLACE LIVE ORDER',key)
    def test_two_market_natural_order_lifecycle_and_actual_fees(self):
        self.ticket();self.b['US'].fill(1,10);self.p.refresh()
        self.ticket(side='SELL',key='b'*32);self.b['US'].fill(1,10);states=self.p.refresh()
        self.assertEqual(states['US']['positions']['US.A']['qty'],0)
        self.assertEqual(states['US']['cash'],198)
        with patch('trading.hk_metadata.execution_metadata',return_value=dict(lot_size=100,tick_size='.005')):
            self.ticket('HK','HK.00001',qty=100,price=2,key='c'*32)
            self.b['HK'].fill(100,2);self.p.refresh()
            self.ticket('HK','HK.00001','SELL',100,2,'d'*32)
            self.b['HK'].fill(100,2);states=self.p.refresh()
        self.assertEqual(states['HK']['positions']['HK.00001']['qty'],0)
        self.assertEqual(states['HK']['cash'],1558)
        self.assertEqual(states['HK']['budget_currency'],'HKD')
        self.assertEqual(states['US']['budget_currency'],'USD')
    def test_unknown_ack_and_request_identity_never_resend(self):
        self.b['US'].fail=True;self.ticket();self.assertEqual(self.b['US'].calls,1)
        with self.assertRaises(SafetyError):self.ticket()
        self.assertEqual(self.b['US'].calls,1)
        self.assertTrue(any(o['status']=='UNKNOWN' for o in self.p.stores['US'].load()['orders'].values()))
    def test_cross_market_pending_order_blocks_other_buy(self):
        self.ticket()
        with patch('trading.hk_metadata.execution_metadata',return_value=dict(lot_size=100,tick_size='.005')):
            with self.assertRaises(SafetyError):self.ticket('HK','HK.00001',qty=100,price=2,key='c'*32)
        self.assertEqual(self.b['HK'].calls,0)
    def test_joint_two_slots_includes_both_markets(self):
        states=self.p.refresh()
        states['US']['positions']={'US.A':{'qty':1}}
        states['HK']['positions']={'HK.00001':{'qty':100}}
        states['HK']['orders']={'pending':dict(code='HK.00002',side='BUY',status='UNKNOWN',fee_final=False)}
        result=self.p.aggregate(states,datetime.now(timezone.utc))
        self.assertEqual(result['occupied_slots'],3);self.assertTrue(result['pending'])
    def test_loss_latch_survives_profit_and_enable(self):
        states=self.p.refresh();states['US']['live_mark']['equity_native']=140
        self.p.aggregate(states,datetime.now(timezone.utc));self.assertTrue(self.p.controls()['loss_latched'])
        states['US']['live_mark']['equity_native']=300;self.p.aggregate(states,datetime.now(timezone.utc))
        with self.assertRaises(SafetyError):self.p.enable(CONFIRM)
    def test_pause_persists_without_gateway_or_portfolio_lock(self):
        with portfolio_lock(self.root):pause_local(self.root)
        self.assertTrue(self.p.controls()['paused'])
        with self.assertRaises(SafetyError):self.ticket()
        self.assertEqual(self.b['US'].calls,0)
    def test_live_stop_cannot_be_cleared_by_enable(self):
        pause_local(self.root,True)
        with self.assertRaises(SafetyError):self.p.enable(CONFIRM)
    def test_stale_fx_blocks_entry_but_allows_cancel_maintenance(self):
        self.ticket();s=self.p.stores['US'].load()
        for o in s['orders'].values():o['created']=(datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat()
        self.p.stores['US'].save(s)
        ref=json.loads((self.base/'fx.json').read_text());ref['as_of']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        private_write(self.base/'fx.json',ref)
        with self.assertRaises(SafetyError):config(self.root,datetime.now(timezone.utc))
        self.p.cycle();self.assertEqual(self.b['US'].remote[0]['status'],'CANCELLED_ALL')
    def test_rebinding_cannot_reset_live_ledger(self):
        with self.assertRaises(SafetyError):self.p.bind()
        self.assertEqual(self.p.stores['US'].load()['initial'],200)
    def test_default_and_enable_confirmation_no_implicit_start(self):
        with self.assertRaises(SafetyError):self.p.enable('yes')
        self.assertFalse(self.c['automatic_entries'])
    def test_no_derivatives_or_wrong_market_and_no_external_sell(self):
        for code in ('HK.00001','US.A261218C10000'):
            with self.assertRaises(SafetyError):self.ticket(code=code,side='SELL')
        with self.assertRaises(SafetyError):self.ticket(side='SELL')
        self.assertEqual(self.b['US'].calls,0)
    def test_live_cash_reserve_and_broker_fees(self):
        self.b['US'].cash=395
        with self.assertRaises(SafetyError):self.ticket()
        self.assertEqual(self.b['US'].calls,0)
    def test_same_day_first_order_is_blocked(self):
        s=self.p.stores['US'].load();s['stocks_enabled_at']=datetime.now(timezone.utc).isoformat();self.p.stores['US'].save(s)
        with self.assertRaises(SafetyError):self.ticket()
        self.assertEqual(self.b['US'].calls,0)
    def test_status_masks_account_ids_and_retains_distinct_currencies(self):
        text=json.dumps(public_status(self.root));self.assertNotIn('111',text);self.assertNotIn('222',text)
        self.assertIn('HKD',text);self.assertIn('USD',text)
    def test_config_change_blocks_orders_without_resetting_binding(self):
        c=dict(self.c,budget_native=999);c['reserve_usd']=391;private_write(self.base/'config.json',c)
        with self.assertRaises(SafetyError):self.ticket()
        self.assertEqual(self.b['US'].calls,0)


class LiveEvidenceTests(unittest.TestCase):
    def test_current_empty_or_reset_simulator_is_not_live_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SafetyError):simulation_evidence(root,'US',datetime.now(timezone.utc))
    def test_draft_never_creates_a_broker_or_sets_live_enabled(self):
        with tempfile.TemporaryDirectory() as root:
            factory=unittest.mock.Mock()
            execute(root,{'action':'template'},factory)
            factory.assert_not_called();self.assertFalse(public_status(root)['enabled'])
            self.assertIsNone(json.loads((Path(root)/'data/trading/live/config.json').read_text())['markets']['US']['budget_native'])


class LiveAdapterTests(unittest.TestCase):
    def broker(self,market):
        from trading.live_broker import LiveBroker
        from types import SimpleNamespace
        from unittest.mock import Mock
        b=LiveBroker.__new__(LiveBroker);b.market=market;b.account='123';b.env='REAL'
        b.m=SimpleNamespace(TrdEnv=SimpleNamespace(REAL='REAL'),TrdSide=SimpleNamespace(BUY='BUY',SELL='SELL'),
            OrderType=SimpleNamespace(NORMAL='NORMAL'),TimeInForce=SimpleNamespace(DAY='DAY'),
            Session=SimpleNamespace(RTH='RTH',NONE='NONE'),Currency=SimpleNamespace(USD='USD',HKD='HKD'))
        b.t=Mock();b.now=lambda:datetime.now(timezone.utc)
        b.book=lambda _:dict(bid=10,ask=10,bid_time=b.now().isoformat(),ask_time=b.now().isoformat())
        b.call=Mock();return b
    def test_explicit_real_account_and_hk_session_no_implicit_defaults(self):
        from unittest.mock import Mock
        for market in ('US','HK'):
            b=self.broker(market);frame=Mock();frame.__len__=Mock(return_value=1);frame.iloc.__getitem__=Mock(return_value={'order_id':'oid'})
            b.call.return_value=frame;b.place(market+'.A','BUY',100,10,'unique')
            kw=b.call.call_args.kwargs
            self.assertEqual(kw['acc_id'],123);self.assertEqual(kw['trd_env'],'REAL')
            self.assertEqual(kw['order_type'],'NORMAL');self.assertFalse(kw['fill_outside_rth'])
            self.assertEqual(kw['session'],'RTH' if market=='US' else 'NONE')
    def test_quote_movement_cannot_chase_limit_or_send_sdk_request(self):
        b=self.broker('US')
        with self.assertRaises(SafetyError):b.place('US.A','BUY',1,9,'unique')
        b.call.assert_not_called()
    def test_missing_or_negative_cash_fields_cannot_use_margin_power(self):
        from unittest.mock import Mock
        b=self.broker('HK');frame=Mock();frame.to_dict.return_value=[dict(hk_cash=1000,hk_avl_withdrawal_cash=500,power=99999)]
        b.call.return_value=frame
        with self.assertRaises(SafetyError):b.settled_cash()
        frame.to_dict.return_value=[dict(hk_cash=1000,hk_avl_withdrawal_cash=500,hkd_net_cash_power=-1)]
        with self.assertRaises(SafetyError):b.settled_cash()
        frame.to_dict.return_value=[dict(hk_cash=1000,hk_avl_withdrawal_cash=500,hkd_net_cash_power=200)]
        self.assertEqual(b.settled_cash(),200)
    def test_final_transport_hook_can_block_a_new_pause(self):
        from unittest.mock import Mock
        b=self.broker('US');b.before_live_send=Mock(side_effect=SafetyError('paused'))
        with self.assertRaises(SafetyError):b.place('US.A','BUY',1,10,'unique')
        b.call.assert_not_called()
    def test_orders_require_a_running_maintenance_worker(self):
        from trading.live_trading import require_worker
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(SafetyError):require_worker(root)

    def test_closed_session_marks_require_current_completed_history(self):
        from trading.moomoo import NY
        b=self.broker('US');b.timezone=NY;b.execution_session=lambda *_args,**_kwargs:None
        day=b.now().astimezone(NY).date();b.completed_session=lambda _:(day-timedelta(days=1)).isoformat()
        b.snapshots=lambda _:[dict(code='US.A',update_time=(b.now()-timedelta(days=1)).isoformat(),last_price=10)]
        self.assertEqual(b.mark('US.A',b.now()),10)
        b.snapshots=lambda _:[dict(code='US.A',update_time=(b.now()-timedelta(days=3)).isoformat(),last_price=10)]
        with self.assertRaises(SafetyError):b.mark('US.A',b.now())
