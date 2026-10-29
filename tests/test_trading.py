import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from trading.policy import *
from trading.store import Store
from trading.engine import Engine as RealEngine

def Engine(*args,**kwargs):
    # Existing execution tests isolate the news dependency; news integration has its own tests.
    return RealEngine(*args,news=lambda code,now:dict(multiplier=1.,event_ids=[],reasons=[]),**kwargs)
from trading.scanner import classification
from trading.option_research import research
from trading.report import weekly_report

NOW=datetime(2026,9,8,13,35,tzinfo=timezone.utc)

def book(bid=10,ask=10.01):
    return dict(bid=bid,ask=ask,bid_time=NOW.isoformat(),ask_time=NOW.isoformat())

def bars(n=60):
    from datetime import date
    return [dict(date=(date(2026,1,1)+timedelta(days=i)).isoformat(),open=10+i,high=11+i,low=9+i,close=10+i,volume=3000000,turnover=40000000) for i in range(n)]

class Fake:
    def __init__(self):
        self.remote=[];self.pos={};self.cash=10000;self.fail=False;self.cancel_fail=False;self.calls=0;self.prices={};self.fees=1
    def bind(self,account,env):
        if account!='chosen' or env not in ('REAL','SIMULATE'):raise SafetyError('Wrong account')
    def positions(self):return dict(self.pos)
    def orders(self,start=None):return [dict(r) for r in self.remote]
    def settled_cash(self):return self.cash
    def mark(self,code,now):return self.prices.get(code,10)
    def fee(self,oid):
        if self.fees is None:raise SafetyError('pending fees')
        return self.fees
    def place(self,code,side,qty,price,tag):
        self.calls+=1;oid=str(self.calls)
        self.remote.append(dict(id=oid,code=code,side=side,qty=qty,filled=0,average=0,status='SUBMITTED',tag=tag))
        if self.fail:raise TimeoutError('Lost acknowledgment')
        return oid
    def cancel(self,oid):
        if self.cancel_fail:raise TimeoutError('Cancel failed')
        r=next(r for r in self.remote if r['id']==oid);r['status']='CANCELLED_PART' if r['filled'] else 'CANCELLED_ALL'
    def fill(self,qty,price=10,status='FILLED_ALL',index=-1):
        r=self.remote[index];delta=qty-r['filled'];r.update(filled=qty,average=price,status=status)
        self.pos[r['code']]=self.pos.get(r['code'],0)+delta*(1 if r['side']=='BUY' else -1)
    def book(self,code):return book()
    def execution_session(self,now,strict=True):return ('2026-09-08','2026-09-04')

class Policies(unittest.TestCase):
    def test_config_caps(self):
        with self.assertRaises(SafetyError):Config(budget_nzd=1001).validate()
    def test_default_live_off(self):self.assertFalse(Config().live_stocks or Config().live_options)
    def test_quote_stale(self):
        with self.assertRaises(SafetyError):quote_price(book(),'BUY',NOW+timedelta(seconds=31))
    def test_quote_future(self):
        with self.assertRaises(SafetyError):quote_price(book(),'BUY',NOW-timedelta(seconds=1))
    def test_wide_spread(self):
        with self.assertRaises(SafetyError):quote_price(book(9,10),'BUY',NOW)
    def test_limit_sides(self):
        self.assertEqual(quote_price(book(),'BUY',NOW),10.01);self.assertEqual(quote_price(book(),'SELL',NOW),10)
    def test_minimum_qty(self):self.assertEqual(whole_qty(10,10,10,3),0)
    def test_fees_in_qty(self):self.assertEqual(whole_qty(100,50,10,3),4)
    def test_history_signal(self):
        b=bars();self.assertTrue(trend(b,b[-1]['date'])['buy'])
    def test_missing_bars(self):
        with self.assertRaises(SafetyError):trend(bars(59),'2026-02-28')
    def test_stale_history(self):
        with self.assertRaises(SafetyError):trend(bars(),'2099-01-01')
    def test_unknown_classification(self):self.assertFalse(eligible(dict(kind='UNKNOWN')))
    def test_tie_sort(self):
        b=bars();m=dict(kind='COMMON',verified=True,exchange='NYSE',suspended=False,delisted=False,lot=1)
        c,_,_=candidates({'US.B':m,'US.A':m},{'US.A':b,'US.B':b},b[-1]['date'])
        self.assertEqual([x['code'] for x in c],['US.A','US.B'])
    def test_preferred_excluded(self):
        d=[{'Symbol':'X','Security Name':'X Preferred Common Stock','ETF':'N','Test Issue':'N','_exchange':'NYSE'}]
        self.assertEqual(classification(d,{'US.X':{}},{},NOW.date())['US.X']['kind'],'UNKNOWN')
    def test_unreviewed_etf_excluded(self):
        d=[{'Symbol':'X','Security Name':'X ETF','ETF':'Y','Test Issue':'N','_exchange':'ARCA'}]
        self.assertEqual(classification(d,{'US.X':{}},{},NOW.date())['US.X']['kind'],'UNKNOWN')
    def test_no_synthetic_option_evidence(self):
        with self.assertRaises(SafetyError):research({'synthetic':True,'source_url':'https://example.com'})
    def option(self):
        return dict(code='US.OPT',underlying='US.A',expiry='2026-10-23',right='CALL',standard=True,multiplier=100,
            delta=.5,open_interest=1000,volume=100,snapshot_date=NOW.date().isoformat(),book=book(.09,.091))
    def signals(self):return {'US.A':dict(liquid=True,buy=True,sell=False,fast=11,slow=10,strength=.2)}
    def test_option_eligible(self):self.assertEqual(len(option_candidates([self.option()],self.signals(),NOW.date(),NOW,15,5)),1)
    def test_option_cap(self):self.assertEqual(option_candidates([self.option()],self.signals(),NOW.date(),NOW,10,5),[])
    def test_adjusted_contract_excluded(self):
        c=self.option();c['multiplier']=150
        self.assertEqual(option_candidates([c],self.signals(),NOW.date(),NOW,100,5),[])
    def test_expiring_contract_excluded(self):
        c=self.option();c['expiry']='2026-09-09'
        self.assertEqual(option_candidates([c],self.signals(),NOW.date(),NOW,100,5),[])

class Orders(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'state.db');self.b=Fake();self.e=Engine(self.b,self.store,Config());self.s=self.e.bind('chosen','SIMULATE',.6,NOW)
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def buy(self,qty=10):return self.e.submit(self.s,'US.A','BUY',qty,10,NOW)
    def test_binding_immutable(self):
        with self.assertRaises(SafetyError):self.e.bind('chosen','SIMULATE',.6,NOW)
    def test_account_mismatch(self):
        self.s['account']='other';self.store.save(self.s)
        with self.assertRaises(SafetyError):self.e.load()
    def test_over_budget(self):
        with self.assertRaises(SafetyError):self.buy(100)
    def test_minimum_cash(self):
        self.b.cash=5
        with self.assertRaises(SafetyError):self.buy()
    def test_external_position(self):
        self.b.pos={'US.A':1}
        with self.assertRaises(SafetyError):self.buy()
    def test_unknown_order_no_retry(self):
        self.b.fail=True;self.buy()
        self.assertEqual(self.b.calls,1)
        self.e.reconcile(self.s,NOW)
        self.assertTrue(self.s['paused'])
        with self.assertRaises(SafetyError):self.buy()
        self.assertEqual(self.b.calls,1)
    def test_partial_fill_idempotent(self):
        self.buy();self.b.fill(4,status='FILLED_PART');self.e.reconcile(self.s,NOW)
        cash=self.s['cash'];self.e.reconcile(self.s,NOW)
        self.assertEqual(self.s['positions']['US.A']['qty'],4);self.assertEqual(self.s['cash'],cash)
    def test_fill_and_fee_accounting(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW)
        self.assertEqual(self.s['cash'],499);self.assertFalse(self.e.blocked_orders(self.s))
    def test_sell_only_owned(self):
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.A','SELL',1,10,NOW)
    def test_roundtrip(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW)
        self.e.submit(self.s,'US.A','SELL',10,12,NOW);self.b.fill(10,12);self.e.reconcile(self.s,NOW)
        self.assertEqual(self.s['cash'],618);self.assertEqual(self.s['positions']['US.A']['qty'],0)
    def test_cancel_partial(self):
        self.buy();self.b.fill(4,status='FILLED_PART');self.e.manage_orders(self.s,NOW+timedelta(seconds=61))
        self.assertEqual(next(iter(self.s['orders'].values()))['status'],'CANCELLED_PART')
        self.assertEqual(self.s['positions']['US.A']['qty'],4)
    def test_cancel_failure_blocks(self):
        self.buy();self.b.cancel_fail=True;self.e.manage_orders(self.s,NOW+timedelta(seconds=61))
        self.assertTrue(self.s['paused']);self.assertTrue(self.e.blocked_orders(self.s))
    def test_loss_limit(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW);self.b.prices['US.A']=6
        self.e.mark(self.s,NOW);self.assertTrue(self.s['paused'])
    def test_paused_can_sell(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW);self.s['paused']=True
        self.e.submit(self.s,'US.A','SELL',10,10,NOW);self.assertEqual(self.b.calls,2)
    def test_live_disabled(self):
        self.s['env']='REAL'
        with self.assertRaises(SafetyError):self.buy()
    def test_two_position_cap(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW)
        self.e.submit(self.s,'US.B','BUY',10,10,NOW);self.b.fill(10);self.e.reconcile(self.s,NOW)
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.C','BUY',1,10,NOW)
    def test_session_claim_survives_restart(self):
        scan=dict(date='2026-09-04',complete=True,signals={},candidates=[dict(code='US.A')])
        self.e.tick(scan,NOW);s=self.store.load();self.assertEqual(len(s['processed']),1)
        Engine(self.b,self.store,Config()).tick(scan,NOW);self.assertEqual(len(self.store.load()['processed']),1)
    def test_incomplete_scan_no_buys(self):
        self.e.tick(dict(date='2026-09-04',complete=False,signals={},candidates=[dict(code='US.A')]),NOW)
        self.assertEqual(self.store.load()['queue'],[])
    def test_fee_pending_live_blocks(self):
        self.buy();self.b.fill(10);self.b.fees=None;self.s['env']='REAL';self.e.reconcile(self.s,NOW)
        self.assertTrue(self.e.blocked_orders(self.s))
    def test_weekly_reconciles(self):
        self.e.mark(self.s,NOW);self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW);self.e.mark(self.s,NOW+timedelta(days=7))
        rows=weekly_report(self.s,Path(self.tmp.name)/'report')
        self.assertAlmostEqual(sum(r['pnl_usd'] for r in rows),-1)


class RuntimeSafety(unittest.TestCase):
    setUp=Orders.setUp
    tearDown=Orders.tearDown
    buy=Orders.buy
    def test_exit_cannot_change_owned_instrument_multiplier(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW)
        with self.assertRaisesRegex(SafetyError,'Exit instrument differs'):
            self.e.submit(self.s,'US.A','SELL',1,10,NOW,kind='OPTION')
        self.assertEqual(self.b.calls,1)
        self.assertEqual(len(self.store.load()['orders']),1)

    def test_stopped_blocks_sells(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW);self.s['stopped']=True
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.A','SELL',10,10,NOW)
    def test_stop_loaded_during_pre_submit_reconciliation_blocks_sell(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW)
        original=self.e.reconcile
        def reconcile_and_stop(state,now):
            original(state,now)
            state['stopped']=True
            self.store.save(state,'stop_during_refresh')
            return state
        self.e.reconcile=reconcile_and_stop
        before=set(self.s['orders'])
        with self.assertRaisesRegex(SafetyError,'Reconciliation stopped'):
            self.e.submit(self.s,'US.A','SELL',10,10,NOW)
        self.assertEqual(self.b.calls,1)
        self.assertEqual(set(self.store.load()['orders']),before)
        self.assertTrue(self.store.load()['stopped'])

    def test_resume_cannot_clear_loss(self):
        self.buy();self.b.fill(10);self.e.reconcile(self.s,NOW);self.b.prices['US.A']=6;self.e.mark(self.s,NOW)
        self.s['paused']=False;self.e.mark(self.s,NOW);self.assertTrue(self.s['paused'])
    def test_unknown_after_crash(self):
        self.buy();s=self.store.load();s['orders'][next(iter(s['orders']))]['id']=None;self.store.save(s)
        e=Engine(self.b,self.store,Config());s=e.load();e.reconcile(s,NOW)
        self.assertEqual(next(iter(s['orders'].values()))['id'],'1');self.assertEqual(self.b.calls,1)
    def test_duplicate_remote_tag(self):
        self.buy();other=dict(self.b.remote[0]);other['id']='different';self.b.remote.append(other)
        self.e.reconcile(self.s,NOW);self.assertTrue(self.s['paused'])
    def test_no_closed_market_submission(self):
        self.b.execution_session=lambda *a,**kw:None
        with self.assertRaises(SafetyError):self.buy()
    def test_partial_fee_revision(self):
        self.buy();self.b.fill(4,status='FILLED_PART');self.e.reconcile(self.s,NOW)
        self.assertEqual(self.s['cash'],557)
        self.b.fill(10);self.e.reconcile(self.s,NOW);self.assertEqual(self.s['cash'],499)
    def test_option_exit_due_queues(self):
        self.s['positions']['US.OPT']=dict(qty=1,kind='OPTION',multiplier=100,cost=10,fees=0,realized=0,expiry='2026-09-15',underlying='US.A',right='CALL')
        self.s['cash']=590;self.b.pos={'US.OPT':1};self.b.prices['US.OPT']=.1;self.store.save(self.s)
        s=self.e.tick(dict(date='2026-09-04',complete=False,signals={},candidates=[]),NOW)
        self.assertEqual(s['queue'][0]['side'],'SELL')
    def test_option_cap_submission(self):
        contract=dict(standard=True,multiplier=100,expiry='2026-10-23',underlying='US.A',right='CALL')
        with self.assertRaises(SafetyError):self.e.submit(self.s,'US.OPT','BUY',1,.2,NOW,'OPTION',contract)

class CalendarTests(unittest.TestCase):
    def broker(self,kind='WHOLE'):
        from trading.moomoo import Moomoo
        b=object.__new__(Moomoo)
        b.calendar=lambda now:[dict(time='2026-09-04',trade_date_type='WHOLE'),dict(time='2026-09-08',trade_date_type=kind)]
        return b
    def test_open_delay(self):
        b=self.broker();self.assertIsNone(b.execution_session(NOW-timedelta(minutes=1)))
        self.assertEqual(b.execution_session(NOW),('2026-09-08','2026-09-04'))
    def test_no_catchup(self):self.assertIsNone(self.broker().execution_session(NOW+timedelta(minutes=5)))
    def test_halfday(self):
        self.assertIsNone(self.broker('MORNING').execution_session(NOW.replace(hour=17),strict=False))
    def test_previous_completed(self):self.assertEqual(self.broker().completed_session(NOW),'2026-09-04')


class AdapterTests(unittest.TestCase):
    def test_quote_context_default_trade_au(self):
        import sys
        from unittest.mock import patch,MagicMock
        from types import SimpleNamespace
        from trading.moomoo import Moomoo
        q,t=MagicMock(),MagicMock()
        sdk=SimpleNamespace(OpenQuoteContext=q,OpenSecTradeContext=t,SecurityFirm=SimpleNamespace(FUTUAU='FUTUAU'),TrdMarket=SimpleNamespace(US='US'))
        with patch.dict(sys.modules,{'moomoo':sdk}),patch('socket.create_connection'),patch('trading.quote_feed.quote_context',return_value=q()) as make_quote:
            b=Moomoo()
            self.assertNotIn('security_firm',make_quote.call_args.kwargs)
            self.assertEqual(t.call_args.kwargs['security_firm'],'FUTUAU')
            b.close()
    def cash_broker(self,env):
        import pandas as pd
        from types import SimpleNamespace
        from trading.moomoo import Moomoo
        b=object.__new__(Moomoo);b.env=env;b.account='1';b.kw=lambda:{}
        b.m=SimpleNamespace(Currency=SimpleNamespace(USD='USD'));b.t=SimpleNamespace(accinfo_query=None)
        b.call=lambda *a,**kw:pd.DataFrame([dict(us_cash=1000,us_avl_withdrawal_cash=0,usd_net_cash_power='N/A')])
        return b
    def test_paper_cash_fields(self):self.assertEqual(self.cash_broker('SIMULATE').settled_cash(),1000)
    def test_real_never_uses_paper_cash_fallback(self):self.assertEqual(self.cash_broker('REAL').settled_cash(),0)
    def test_read_only_doctor_independent_failures(self):
        from trading.cli import doctor
        from types import SimpleNamespace
        def fail(*a,**kw):raise SafetyError('permission')
        b=SimpleNamespace(q=SimpleNamespace(get_global_state=None,get_history_kl_quota=None),call=fail,snapshots=fail,history=fail,completed_session=fail,
            accounts=lambda:[])
        r=doctor(b);self.assertFalse(r['quotes']['ok']);self.assertTrue(r['accounts']['ok'])

if __name__=='__main__':unittest.main()
