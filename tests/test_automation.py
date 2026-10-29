import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
from trading.automation import ScanSchedule,Supervisor,session_schedule
from trading.rolling_scan import atomic
from trading.policy import SafetyError,Config
from trading.paper_strategy import handoff,probe_ready,strategy_evidence,current_scan
from trading.store import Store
from trading.engine import Engine
from trading.quote_feed import QuoteUnavailable
from test_trading import Fake,NOW,book

class Scheduling(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'schedule.json';self.market=self.root/'market'
        self.c=Mock();self.c.running.return_value=False;self.s=ScanSchedule(self.c,self.path,self.market)
    def tearDown(self):self.tmp.cleanup()
    def tick(self,now=NOW):return self.s.tick(now,'2026-09-04','2026-09-08T16:05:00-04:00')
    def test_complete_skips_spawn(self):
        atomic(self.market/'scan.json',dict(date='2026-09-04',complete=True));self.assertEqual(self.tick()['status'],'complete');self.c.start.assert_not_called()
    def test_restart_and_manual_worker_do_not_duplicate(self):
        self.c.running.return_value=True;self.assertEqual(self.tick()['status'],'running');self.c.start.assert_not_called()
        self.s=ScanSchedule(self.c,self.path,self.market);self.tick();self.c.start.assert_not_called()
    def test_retry_budget_survives_restart(self):
        now=NOW;self.tick(now)
        for delay in (300,900,1800):
            now+=timedelta(seconds=30);state=self.tick(now)
            self.assertEqual(state['status'],'retry_wait')
            retry=datetime.fromisoformat(state['retry_at']);self.assertEqual((retry-now).total_seconds(),delay)
            self.s=ScanSchedule(self.c,self.path,self.market)
            self.assertEqual(self.tick(retry-timedelta(seconds=1))['status'],'retry_wait')
            now=retry;self.assertEqual(self.tick(now)['status'],'running')
        self.assertEqual(self.tick(now+timedelta(seconds=30))['status'],'needs_attention')
        self.tick(now+timedelta(days=1));self.assertEqual(self.c.start.call_count,4)
    def test_failed_spawn_consumes_bounded_attempt(self):
        self.c.start.side_effect=OSError('OpenD disconnected');self.tick()
        self.assertEqual(self.tick(NOW+timedelta(seconds=30))['status'],'retry_wait')
    def test_partial_result_never_counts_as_success(self):
        atomic(self.market/'scan_progress.json',dict(date='2026-09-04',complete=False))
        self.assertEqual(self.tick()['status'],'running')
    def test_next_date_resets_retry_budget(self):
        atomic(self.path,dict(target_date='2026-09-03',status='needs_attention',retries=3))
        self.assertEqual(self.tick()['retries'],0);self.c.start.assert_called_once()
    def test_calendar_weekend_holiday_and_close_buffer(self):
        rows=[dict(time=d,trade_date_type='WHOLE') for d in ('2026-09-04','2026-09-08','2026-09-09')]
        for hour,minute,expected in [(20,4,'2026-09-04'),(20,5,'2026-09-08')]:
            now=datetime(2026,9,8,hour,minute,tzinfo=timezone.utc)
            self.assertEqual(session_schedule(rows,now)[0],expected)
        self.assertEqual(session_schedule(rows,datetime(2026,9,7,22,tzinfo=timezone.utc))[0],'2026-09-04')
    def test_half_day_close(self):
        rows=[dict(time='2026-11-25',trade_date_type='WHOLE'),dict(time='2026-11-27',trade_date_type='MORNING'),dict(time='2026-11-30',trade_date_type='WHOLE')]
        self.assertEqual(session_schedule(rows,datetime(2026,11,27,18,5,tzinfo=timezone.utc))[0],'2026-11-27')
    def test_dst_changes_utc_schedule(self):
        rows=[dict(time=d,trade_date_type='WHOLE') for d in ('2026-03-05','2026-03-06','2026-03-09','2026-03-10')]
        _,next_run=session_schedule(rows,datetime(2026,3,7,tzinfo=timezone.utc))
        self.assertEqual(datetime.fromisoformat(next_run).astimezone(timezone.utc).hour,20)
        self.assertEqual(session_schedule(rows,datetime(2026,3,6,21,4,tzinfo=timezone.utc))[0],'2026-03-05')
        self.assertEqual(session_schedule(rows,datetime(2026,3,6,21,5,tzinfo=timezone.utc))[0],'2026-03-06')
    def test_unknown_calendar_not_guessed(self):
        with self.assertRaises(SafetyError):session_schedule([],NOW)

class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=Store(self.root/'paper.sqlite');self.b=Fake()
        orders={}
        for i,(side,status,qty) in enumerate([('BUY','CANCELLED_ALL',0),('BUY','FILLED_ALL',1),('SELL','FILLED_ALL',1)]):
            oid=str(i);orders[oid]=dict(id=oid,code='US.AA',side=side,status=status,qty=1,booked_qty=qty,fee_final=True,cancel_requested=i==0)
            self.b.remote.append(dict(id=oid,code='US.AA',side=side,status=status,qty=1,filled=qty,average=10 if qty else 0))
        self.probe=dict(account='chosen',env='SIMULATE',purpose='transport_probe',probe_phase='complete',paused=False,stopped=False,
            orders=orders,positions={},alerts=[],marks=[{}],bound_at='2026-09-08T13:30:00+00:00')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_clean_handoff_is_atomic_and_delays_entries(self):
        s=handoff(self.b,self.db,self.probe,NOW)
        self.assertEqual(s['purpose'],'strategy_simulation');self.assertEqual(s['entry_after_session'],'2026-09-08')
        self.assertEqual(s['cash'],600);self.assertFalse(s['enabled_stocks']);self.assertEqual(s['orders'],{})
    def test_duplicate_handoff_preserves_cash_and_pauses(self):
        s=handoff(self.b,self.db,self.probe,NOW);s.update(cash=590,paused=True,stopped=True,alerts=['manual']);self.db.save(s)
        self.probe['stopped']=True
        again=handoff(self.b,self.db,self.probe,NOW+timedelta(days=1))
        self.assertEqual(again['cash'],590);self.assertTrue(again['paused']);self.assertTrue(again['stopped'])
    def test_account_mismatch_not_reset(self):
        handoff(self.b,self.db,self.probe,NOW);self.probe['account']='other'
        with self.assertRaises(SafetyError):handoff(self.b,self.db,self.probe,NOW)
        self.assertEqual(self.db.load()['account'],'chosen')
    def test_remaining_position_blocks_handoff(self):
        self.b.pos={'US.AA':1}
        with self.assertRaises(SafetyError):handoff(self.b,self.db,self.probe,NOW)
        self.assertFalse(self.db.exists())
    def test_unknown_order_and_quantity_mismatch_block(self):
        self.b.remote[0]['id']='external'
        with self.assertRaises(SafetyError):handoff(self.b,self.db,self.probe,NOW)
        self.b.remote[0]['id']='0';self.b.remote[1]['filled']=.5
        with self.assertRaises(SafetyError):handoff(self.b,self.db,self.probe,NOW)
    def test_unfinished_or_manually_paused_probe_not_accepted(self):
        for field,value in [('probe_phase','buy_wait'),('paused',True),('stopped',True),('env','REAL')]:
            candidate=dict(self.probe,**{field:value});self.assertFalse(probe_ready(candidate))
    def test_supervisor_waits_for_probe_process_exit(self):
        root=self.root;c=Mock();c.running.side_effect=lambda key:key=='probe'
        superv=Supervisor(c,root);superv.calendar=Mock();superv.calendar.read.side_effect=SafetyError('offline')
        with patch('trading.automation.ledger',side_effect=lambda p:self.probe if p.name=='probe.sqlite' else {}):
            state=superv.tick(NOW)
        self.assertEqual(state['handoff'],'waiting_probe_exit');c.start.assert_not_called()
    def test_supervisor_automatically_starts_strategy(self):
        c=Mock();c.running.return_value=False;superv=Supervisor(c,self.root);superv.calendar=Mock();superv.calendar.read.side_effect=SafetyError('offline')
        with patch('trading.automation.ledger',side_effect=lambda p:self.probe if p.name=='probe.sqlite' else {}):superv.tick(NOW)
        c.start.assert_called_once_with('strategy')

class StrategyFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=Store(self.root/'state.sqlite');self.b=Fake()
        self.e=Engine(self.b,self.db,Config(),news=lambda c,n:dict(multiplier=1))
        self.s=self.e.bind('chosen','SIMULATE',.6,NOW)
        self.scan=dict(date='2026-09-04',complete=True,signals={},candidates=[dict(code='US.A'),dict(code='US.B'),dict(code='US.C')])
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_only_two_ranked_slots_no_substitution(self):
        self.e.tick(self.scan,NOW);self.assertEqual([o['code'] for o in self.db.load()['queue']],['US.A','US.B'])
        self.b.book=lambda c:book(1,20)
        self.e.drain(self.scan,NOW);self.e.drain(self.scan,NOW)
        self.assertEqual(self.db.load()['queue'],[]);self.assertEqual(self.b.calls,0)
    def test_handoff_session_cannot_buy(self):
        self.s['entry_after_session']='2026-09-08';self.db.save(self.s);self.e.tick(self.scan,NOW)
        self.assertEqual(self.db.load()['queue'],[])
    def test_initial_quote_wait_preserves_item_and_timeout_no_order(self):
        self.e.tick(self.scan,NOW)
        self.b.book=Mock(side_effect=QuoteUnavailable('cache','waiting_quote'))
        self.e.drain(self.scan,NOW)
        self.assertEqual(self.db.load()['queue'][0]['code'],'US.A')
        self.b.book=Mock(return_value=book())
        self.e.drain(self.scan,NOW+timedelta(seconds=120))
        self.assertEqual(self.db.load()['queue'][0]['code'],'US.B');self.assertEqual(self.b.calls,0)
    def test_quote_update_then_single_submission(self):
        self.e.tick(self.scan,NOW);self.b.book=Mock(side_effect=[QuoteUnavailable('cache','waiting_quote'),book(),book()])
        self.e.drain(self.scan,NOW);self.e.drain(self.scan,NOW)
        self.assertEqual(self.b.calls,1);self.assertEqual(len(self.db.load()['orders']),1)
    def test_second_quote_expiry_restores_unclaimed_item(self):
        self.e.tick(self.scan,NOW);self.b.book=Mock(side_effect=[book(),QuoteUnavailable('cache','quote_expired')])
        self.e.drain(self.scan,NOW);self.assertEqual(self.db.load()['orders'],{});self.assertEqual(self.db.load()['queue'][0]['code'],'US.A')
    def test_wait_still_services_expired_order_cancellation(self):
        self.e.tick(self.scan,NOW);s=self.db.load();self.e.submit(s,'US.OLD','BUY',1,10,NOW);next(iter(s['orders'].values()))['created']=(NOW-timedelta(seconds=61)).isoformat();self.db.save(s)
        self.b.book=Mock(side_effect=QuoteUnavailable('cache','waiting_quote'))
        self.e.drain(self.scan,NOW)
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL');self.assertIn('quote_wait',self.db.load())
    def test_scan_incomplete_services_sell_but_no_entry(self):
        s=self.db.load();self.e.submit(s,'US.A','BUY',1,10,NOW);self.b.fill(1);self.e.reconcile(s,NOW)
        scan=dict(self.scan,complete=False,signals={'US.A':{'sell':True}});self.e.tick(scan,NOW)
        self.assertEqual([i['side'] for i in self.db.load()['queue']],['SELL'])
    def test_stale_published_scan_not_used_for_entries(self):
        market=self.root/'data/trading/market';atomic(market/'scan.json',self.scan);b=Mock();b.completed_session.return_value='2026-09-08'
        self.assertFalse(current_scan(self.root,b,NOW)['complete'])
    def test_probe_and_nonflat_lifecycles_not_strategy_evidence(self):
        self.s['purpose']='transport_probe';self.assertFalse(strategy_evidence(self.s))
        self.s['purpose']='strategy_simulation';self.s['marks']=[{}];self.assertFalse(strategy_evidence(self.s))
    def test_natural_buy_sell_produces_evidence(self):
        s=self.s;s['purpose']='strategy_simulation';self.db.save(s)
        self.e.submit(s,'US.A','BUY',1,10,NOW);self.b.fill(1);self.e.reconcile(s,NOW)
        self.e.submit(s,'US.A','SELL',1,10,NOW);self.b.fill(1);self.e.reconcile(s,NOW);self.e.mark(s,NOW)
        self.assertTrue(strategy_evidence(s));s['paused']=True;self.assertFalse(strategy_evidence(s))

    def test_manual_exit_is_not_natural_strategy_evidence(self):
        s=self.s;s['purpose']='strategy_simulation';self.db.save(s)
        self.e.submit(s,'US.A','BUY',1,10,NOW);self.b.fill(1);self.e.reconcile(s,NOW)
        self.e.submit(s,'US.A','SELL',1,10,NOW,execution_purpose='manual_exit_test')
        self.b.fill(1);self.e.reconcile(s,NOW);self.e.mark(s,NOW)
        self.assertEqual(list(self.db.load()['orders'].values())[-1]['execution_purpose'],'manual_exit_test')
        self.assertFalse(strategy_evidence(s))

    def test_quote_wait_timeout_survives_worker_restart(self):
        self.e.tick(self.scan,NOW);self.b.book=Mock(side_effect=QuoteUnavailable('cache','waiting_quote'))
        self.e.drain(self.scan,NOW)
        restarted=Engine(self.b,self.db,Config(),news=lambda c,n:dict(multiplier=1))
        restarted.drain(self.scan,NOW+timedelta(seconds=121))
        self.assertEqual(self.b.calls,0);self.assertEqual(self.db.load()['queue'][0]['code'],'US.B')
    def test_control_arriving_after_queue_claim_blocks_submission(self):
        self.e.tick(self.scan,NOW);self.e.controls=lambda:dict(paused=True)
        self.e.drain(self.scan,NOW)
        self.assertEqual(self.b.calls,0);self.assertTrue(self.db.load()['paused'])
    def test_stopped_worker_cancels_existing_order(self):
        self.e.submit(self.s,'US.A','BUY',1,10,NOW);self.s['stopped']=True;self.db.save(self.s)
        self.e.tick(self.scan,NOW)
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL');self.assertTrue(self.db.load()['stopped'])
    def test_same_day_missing_held_signal_blocks_new_entries(self):
        self.e.submit(self.s,'US.OWNED','BUY',1,10,NOW);self.b.fill(1);self.e.reconcile(self.s,NOW)
        self.e.tick(self.scan,NOW);self.assertEqual(self.db.load()['queue'],[])
        self.assertIn('US.OWNED',self.db.load()['data_block'])
    def test_expired_news_does_not_open_a_position(self):
        self.e.news=lambda c,n:dict(multiplier=0,reasons=['News sources stale'])
        self.e.tick(self.scan,NOW);self.e.drain(self.scan,NOW)
        self.assertEqual(self.b.calls,0)
    def test_raw_evidence_persists_separately_from_state(self):
        self.db.event('broker_cancel',dict(return_code=0,response=[dict(order_status='CANCELLED_ALL')]))
        row=self.db.db.execute("SELECT body FROM events WHERE kind='broker_cancel'").fetchone()
        self.assertEqual(json.loads(row[0])['response'][0]['order_status'],'CANCELLED_ALL')
        self.assertFalse(self.db.load()['enabled_stocks'])

    def test_idle_strategy_queries_positions_once_per_cycle(self):
        self.s['entry_after_session']='2026-09-08';self.db.save(self.s)
        with patch.object(self.b,'positions',wraps=self.b.positions) as positions:
            self.e.tick(self.scan,NOW);self.e.drain(self.scan,NOW)
        self.assertEqual(positions.call_count,1)
    def test_cancel_still_reconciles_ack_after_fresh_precheck(self):
        self.e.submit(self.s,'US.A','BUY',1,10,NOW)
        with patch.object(self.b,'positions',wraps=self.b.positions) as positions:
            self.e.tick(self.scan,NOW+timedelta(seconds=61))
        self.assertEqual(positions.call_count,2)
        self.assertEqual(next(iter(self.db.load()['orders'].values()))['status'],'CANCELLED_ALL')

if __name__=='__main__':unittest.main()
