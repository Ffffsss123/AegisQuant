import tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from trading.swing_model import features,snapshot,size,exit_reason,VERSION,PARAMETERS
from trading.swing_engine import SwingEngine
from trading.swing_research import run,signal_history
from trading.policy import Config,SafetyError
from trading.store import Store
from trading.moomoo import NY
from tests import test_trading as helper

class Model(unittest.TestCase):
    def test_breakout_and_atr(self):
        bars=helper.bars();bars[-1].update(close=70,high=71)
        f=features(bars,bars[-1]['date'])
        self.assertTrue(f['buy']);self.assertGreater(f['atr'],2)
        self.assertAlmostEqual(f['target_distance'],2*f['stop_distance'])
    def test_trend_without_breakout(self):
        b=helper.bars();self.assertTrue(features(b,b[-1]['date'])['base_buy']);self.assertFalse(features(b,b[-1]['date'])['buy'])
    def test_future_bar_rejected(self):
        b=helper.bars();day=b[-1]['date'];b.append(dict(b[-1],date='2026-03-20'))
        with self.assertRaises(SafetyError):features(b,day)
    def test_parent_universe_and_missing_history(self):
        b=helper.bars();b[-1].update(close=70,high=71)
        scan=dict(date=b[-1]['date'],generated_at='stamp',complete=True,signals={'US.A':{},'US.B':{}},candidates=[{'code':'US.A'}])
        result=snapshot(scan,{'US.A':b,'US.B':b});self.assertEqual([c['code'] for c in result['candidates']],['US.A'])
        self.assertFalse(snapshot(scan,{'US.A':b})['complete'])
    def test_risk_cash_and_cost_edges(self):
        sig=dict(stop_distance=1,target_distance=2)
        self.assertEqual(size(600,600,10,sig),8)
        self.assertEqual(size(600,600,10,sig,remaining_risk=7),0)
        self.assertEqual(size(12,600,10,sig),0)
        self.assertEqual(size(600,600,10,dict(stop_distance=.1,target_distance=.2)),0)
        self.assertLessEqual(size(600,600,10,sig)*10+3,270)
    def test_exit_priority(self):
        p=dict(swing_stop=9,swing_target=12)
        self.assertEqual(exit_reason(p,None,8,1,False),'stop')
        self.assertEqual(exit_reason(p,None,12,1,False),'target')
        self.assertIsNone(exit_reason(p,None,10,5,False))
        self.assertEqual(exit_reason(p,None,10,5,True),'max_sessions')

class Broker(helper.Fake):
    def __init__(self):super().__init__();self.now_value=helper.NOW;self.quote_bad=False
    def calendar(self,now):
        return [dict(time=d,trade_date_type='WHOLE') for d in ['2026-09-02','2026-09-03','2026-09-04','2026-09-08','2026-09-09','2026-09-10','2026-09-11','2026-09-14','2026-09-15']]
    def session_times(self,day,kind):
        open=datetime.fromisoformat(day+'T09:30').replace(tzinfo=NY)
        return open,open+timedelta(hours=3.5 if kind=='HALF' else 6.5)
    def execution_session(self,now,strict=True):
        day=now.astimezone(NY).date().isoformat();dates=[r['time'] for r in self.calendar(now)]
        if day not in dates:return None
        op,cl=self.session_times(day,'WHOLE')
        if not op+timedelta(minutes=5)<=now<cl:return None
        if strict and now>=op+timedelta(minutes=10):return None
        return day,dates[dates.index(day)-1]
    def book(self,code):
        if self.quote_bad:raise SafetyError('Stale/future quote')
        return dict(bid=10,ask=10.01,bid_time=self.now_value.isoformat(),ask_time=self.now_value.isoformat())

class Execution(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'s.db');self.b=Broker()
        self.e=SwingEngine(self.b,self.db,Config(),news=lambda c,n:dict(multiplier=1))
        self.s=self.e.bind('chosen','SIMULATE',.6,helper.NOW,extra=dict(strategy_version=VERSION,strategy_parameters=PARAMETERS,entry_after_session='2026-09-04'))
        self.scan=dict(date='2026-09-04',version=VERSION,complete=True,signals={'US.A':dict(sell=False,stop_distance=1,target_distance=2)},candidates=[{'code':'US.A'}])
        self.e.active_scan=self.scan
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def buy(self,qty=2):
        tag=self.e.submit(self.s,'US.A','BUY',qty,10.01,helper.NOW)
        self.b.fill(qty,10.01);self.e.reconcile(self.s,helper.NOW);return tag
    def test_no_real_binding(self):
        self.s['env']='REAL';self.db.save(self.s)
        with self.assertRaises(SafetyError):self.e.load()
    def test_partial_fill_levels_and_restart(self):
        self.e.submit(self.s,'US.A','BUY',2,10.01,helper.NOW);self.b.fill(1,10,status='FILLED_PART')
        self.e.reconcile(self.s,helper.NOW);self.assertEqual(self.s['positions']['US.A']['swing_stop'],9)
        self.b.fill(2,10.01);self.e.reconcile(self.s,helper.NOW)
        self.assertAlmostEqual(self.db.load()['positions']['US.A']['swing_target'],12.01)
        cash=self.s['cash'];self.e.reconcile(self.s,helper.NOW);self.assertEqual(self.s['cash'],cash)
    def test_missing_trend_blocks_entries(self):
        self.buy();scan=dict(self.scan,signals={});s=self.e.tick(scan,helper.NOW)
        self.assertIn('趋势数据缺失',s['data_block']);self.assertFalse(s['queue'])
    def test_bad_quote_blocks_entries_then_recovers(self):
        self.buy();self.b.quote_bad=True;s=self.e.tick(self.scan,helper.NOW)
        self.assertIn('US.A',s['exit_quote_status'])
        self.b.quote_bad=False;s=self.e.tick(self.scan,helper.NOW+timedelta(seconds=5));self.assertFalse(s['exit_quote_status'])
    def test_paused_trend_exit_and_no_duplicate_after_restart(self):
        self.buy();self.s['paused']=True;self.db.save(self.s)
        self.scan['signals']['US.A']['sell']=True
        s=self.e.tick(self.scan,helper.NOW);self.assertEqual(len(s['queue']),1);self.assertEqual(s['queue'][0]['side'],'SELL')
        e=SwingEngine(self.b,self.db,Config(),news=lambda c,n:dict(multiplier=1))
        self.assertEqual(len(e.tick(self.scan,helper.NOW)['queue']),1)
    def test_fifth_session_weekend_and_half_day(self):
        self.buy();now=datetime(2026,9,14,16,46,tzinfo=timezone.utc);self.b.now_value=now
        original=self.b.calendar
        self.b.calendar=lambda n:[dict(r,trade_date_type='HALF' if r['time']=='2026-09-14' else 'WHOLE') for r in original(n)]
        s=self.e.tick(dict(self.scan,date='2026-09-11'),now)
        self.assertEqual(s['positions']['US.A']['holding_sessions'],5)
        self.assertEqual(s['queue'][0]['exit_reason'],'max_sessions')
    def test_exit_failure_pauses_and_not_repriced(self):
        self.buy();self.scan['signals']['US.A']['sell']=True;self.e.tick(self.scan,helper.NOW)
        self.b.book=lambda c:dict(helper.book(9,10),bid_time=(helper.NOW-timedelta(seconds=31)).isoformat())
        self.e.drain(self.scan,helper.NOW);s=self.db.load()
        self.assertTrue(s['queue']);self.assertEqual(self.b.calls,1)
        self.e.drain(self.scan,helper.NOW+timedelta(seconds=120));s=self.db.load()
        self.assertTrue(s['paused']);self.assertFalse(s['queue'])
        self.e.tick(self.scan,helper.NOW);self.assertFalse(self.db.load()['queue'])
    def test_expired_entry_window_drops_queue(self):
        self.e.tick(self.scan,helper.NOW)
        self.e.drain(self.scan,helper.NOW+timedelta(minutes=6))
        self.assertEqual(self.b.calls,0);self.assertFalse(self.db.load()['queue'])
    def test_pending_cancel_precedes_quote_decisions(self):
        self.e.submit(self.s,'US.A','BUY',2,10.01,helper.NOW);self.b.quote_bad=True
        self.e.tick(self.scan,helper.NOW+timedelta(seconds=61))
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL')
    def test_handoff_delay_no_new_entries(self):
        self.s['entry_after_session']='2026-09-08';self.db.save(self.s)
        self.assertFalse(self.e.tick(self.scan,helper.NOW)['queue'])

class Research(unittest.TestCase):
    def data(self):
        days=['2026-09-08','2026-09-09','2026-09-10','2026-09-11','2026-09-14','2026-09-15']
        histories={'US.A':[dict(date=d,open=10,close=10,high=10.1,low=9.9) for d in days]}
        sig=dict(buy=True,base_buy=True,sell=False,liquid=True,strength=1,stop_distance=1,target_distance=2)
        return histories,days,{d:{'US.A':sig} for d in days}
    def test_stop_before_target_on_ambiguous_bar(self):
        h,d,s=self.data();h['US.A'][0].update(high=13,low=8)
        r=run(h,d[:1],'swing',friction=0,signals_by_day=s)
        self.assertEqual(r['trade_log'][1]['reason'],'stop');self.assertEqual(r['trade_log'][1]['price'],9)
    def test_gap_and_no_forced_final_exit(self):
        h,d,s=self.data();r=run(h,d[:1],'swing',signals_by_day=s);self.assertEqual(r['open_positions'],1);self.assertEqual(r['roundtrips'],0)
        h['US.A'][1].update(open=7,low=6,close=7,high=8)
        r=run(h,d[:2],'swing',friction=0,signals_by_day=s);self.assertEqual(r['trade_log'][1]['price'],7)
    def test_five_sessions_including_weekend(self):
        h,d,s=self.data();r=run(h,d[:5],'swing',signals_by_day=s)
        self.assertEqual(r['trade_log'][1]['day'],'2026-09-14');self.assertEqual(r['max_holding_sessions'],5)
    def test_today_close_cannot_change_opening_order(self):
        h,d,s=self.data();h['US.B']=[dict(b) for b in h['US.A']]
        s[d[1]]['US.B']=dict(s[d[0]]['US.A'],strength=2)
        r=run(h,d[:2],'swing',signals_by_day=s)
        h['US.A'][1].update(close=1,low=1)
        r2=run(h,d[:2],'swing',signals_by_day=s)
        self.assertEqual([o for o in r['trade_log'] if o['side']=='BUY'],[o for o in r2['trade_log'] if o['side']=='BUY'])
    def test_cost_stress_reduces_equity_for_same_trade(self):
        h,d,s=self.data();a=run(h,d[:5],'swing',friction=.0015,signals_by_day=s);b=run(h,d[:5],'swing',friction=.003,signals_by_day=s)
        self.assertLess(b['ending_equity_usd'],a['ending_equity_usd'])
    def test_signals_cannot_see_execution_bar(self):
        bars=helper.bars(62);days=[b['date'] for b in bars]
        original=signal_history({'US.A':bars},days)
        bars[60].update(close=999,high=1000)
        changed=signal_history({'US.A':bars},days)
        self.assertEqual(original[days[60]],changed[days[60]])

if __name__=='__main__':unittest.main()

class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'s.db');self.b=Broker()
        self.e=helper.Engine(self.b,self.db,Config())
        self.s=self.e.bind('chosen','SIMULATE',.6,helper.NOW,extra=dict(purpose='strategy_simulation',entry_after_session='2026-09-11'))
        self.probe=dict(account='chosen',orders={});self.scan=dict(complete=True,version=VERSION)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def adopt(self):
        from trading.swing_engine import adopt_empty_simulation
        return adopt_empty_simulation(self.b,self.db,self.probe,self.scan,helper.NOW)
    def test_preserves_controls_cash_and_history_idempotently(self):
        self.s['paused']=True;self.s['stopped']=True;self.s['alerts']=['User pause'];self.db.save(self.s)
        result=self.adopt()
        for k in ('cash','bound_at','initial','entry_after_session','paused','stopped','alerts'):self.assertEqual(result[k],self.s[k])
        self.assertEqual(self.adopt()['strategy_migrations'],result['strategy_migrations'])
    def test_unknown_completed_broker_order_refuses_migration(self):
        self.b.remote=[dict(id='external',status='FILLED_ALL')]
        with self.assertRaises(SafetyError):self.adopt()
        self.assertNotIn('strategy_version',self.db.load())
    def test_existing_local_trade_refuses_reset(self):
        self.s['orders']={'old':{}};self.db.save(self.s)
        with self.assertRaises(SafetyError):self.adopt()
    def test_incomplete_signals_refuses_activation(self):
        self.scan['complete']=False
        with self.assertRaises(SafetyError):self.adopt()
