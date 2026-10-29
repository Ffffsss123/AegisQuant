import unittest,tempfile
from pathlib import Path
from trading.engine import Engine
from trading.store import Store
from trading.policy import Config,SafetyError
from trading.simulation_reconcile import reconcile_engines
from tests.test_trading import Fake,NOW

class Coordinated(unittest.TestCase):
    def test_native_swing_stop_queue_preserves_peer_option_inventory(self):
        from tests.test_swing import Broker
        from trading.swing_engine import SwingEngine
        from trading.swing_model import VERSION
        from trading.simulation_reconcile import account_session
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);broker=Broker();paths={k:root/(k+'.sqlite') for k in ('stock','option')}
            for owner,path in paths.items():
                db=Store(path)
                state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
                code='US.A' if owner=='stock' else 'US.O'
                state['positions']={code:dict(qty=1,kind='STOCK' if owner=='stock' else 'OPTION',multiplier=1 if owner=='stock' else 100,cost=10,realized=0,fees=0,entry_session='2026-09-08',swing_stop=11,swing_target=15)}
                state.update(paused=True,capital_mode='independent_sleeves_v1')
                if owner=='stock':state.update(strategy_version=VERSION,entry_after_session='2026-09-04')
                db.save(state);db.close()
            broker.pos={'US.A':1,'US.O':1}
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            with account_session(root,broker,paths,account='chosen',archive=archive) as (states,evidence,engines):
                engine=engines['stock'];engine.news=lambda c,n:dict(multiplier=1)
                engine.tick(dict(date='2026-09-04',version=VERSION,complete=True,signals={'US.A':{'sell':False}},candidates=[]),NOW)
                saved=engine.store.load()
                self.assertEqual([(q['code'],q['side'],q['exit_reason']) for q in saved['queue']],[('US.A','SELL','stop')])
                self.assertTrue(saved['paused'])
                self.assertFalse(any('inventory' in str(a).lower() for a in saved['alerts']))
                self.assertEqual(engines['option'].store.load()['positions']['US.O']['qty'],1)
                self.assertEqual(broker.calls,0)
                controls=root/'data/trading/simulation_controls.json'
                controls.write_text('{"paused":true,"stopped":false,"capital_migration_pending":true}')
                with self.assertRaisesRegex(SafetyError,'cannot mutate broker orders'):
                    engine.submit(saved,'US.A','SELL',1,10,NOW,exit_reason='stop')
                self.assertEqual(engine.store.load()['orders'],{})
                self.assertFalse(any('Submission uncertain' in str(a) for a in engine.store.load()['alerts']))
                before=engine.store.load()
                with self.assertRaisesRegex(SafetyError,'cannot mutate broker orders'):
                    engine.drain(dict(complete=True,date='2026-09-04'),NOW)
                self.assertEqual(engine.store.load(),before)
                self.assertEqual(broker.calls,0)
                timestamp=evidence['query_started_at']
                evidence['query_started_at']='2000-01-01T00:00:00+00:00'
                with self.assertRaisesRegex(SafetyError,'evidence expired'):
                    engine.before_order_intent(engine.store.load(),dict(code='US.A',side='SELL',kind='STOCK',qty=1))
                evidence['query_started_at']=timestamp
                with self.assertRaisesRegex(SafetyError,'stock exits only'):
                    engine.before_order_intent(engine.store.load(),dict(code='US.A',side='BUY',kind='STOCK',qty=1))
                # Fixture-only transport: production session remains read-only.
                engine.b=broker
                from trading.quote_feed import QuoteUnavailable
                native_submit=engine.submit
                def quote_failure_after_refresh(state,*args,**kwargs):
                    engine.reconcile(state,NOW)
                    raise QuoteUnavailable('Waiting for fresh book','connection_interrupted')
                engine.submit=quote_failure_after_refresh
                engine.drain(dict(complete=True,date='2026-09-04'),NOW)
                waiting=engine.store.load()
                self.assertEqual(len(waiting['queue']),1)
                self.assertEqual(waiting['queue'][0]['exit_reason'],'stop')
                self.assertFalse(waiting['orders'])
                self.assertEqual(broker.calls,0)
                engine.submit=native_submit
                broker.fail=True  # Accepted remotely, acknowledgement lost.
                engine.drain(dict(complete=True,date='2026-09-04'),NOW)
                uncertain=engine.store.load()
                self.assertEqual(next(iter(uncertain['orders'].values()))['status'],'UNKNOWN')
                self.assertEqual(broker.calls,1)
                broker.fail=False
                engine.reconcile(uncertain,NOW)
                submitted=engine.store.load()
                self.assertEqual(broker.calls,1)
                order=next(iter(submitted['orders'].values()))
                self.assertEqual((order['side'],order['status'],order['exit_reason']),('SELL','SUBMITTED','stop'))
                broker.fill(1,10)
                engine.reconcile(submitted,NOW)
                self.assertEqual(submitted['positions']['US.A']['qty'],0)
                self.assertEqual(engines['option'].store.load()['positions']['US.O']['qty'],1)
                self.assertTrue(submitted['paused'])
                self.assertFalse(submitted['queue'])

    def test_scoped_stock_exit_lost_ack_cannot_resend(self):
        from tests.test_swing import Broker
        from trading.swing_engine import SwingEngine
        from trading.swing_model import VERSION
        from trading.simulation_reconcile import account_session
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);broker=Broker();paths={k:root/(k+'.sqlite') for k in ('stock','option')}
            for owner,path in paths.items():
                db=Store(path)
                state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
                code='US.A' if owner=='stock' else 'US.O'
                state['positions']={code:dict(qty=1,kind='STOCK' if owner=='stock' else 'OPTION',multiplier=1 if owner=='stock' else 100,cost=10,realized=0,fees=0,entry_session='2026-09-08',swing_stop=11,swing_target=15)}
                state.update(paused=True,capital_mode='independent_sleeves_v1')
                if owner=='stock':state.update(strategy_version=VERSION,entry_after_session='2026-09-04')
                db.save(state);db.close()
            broker.pos={'US.A':1,'US.O':1}
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            with account_session(root,broker,paths,account='chosen',archive=archive,stock_exit_owner='stock') as (states,evidence,engines):
                engine=engines['stock'];engine.news=lambda c,n:dict(multiplier=1)
                engine.tick(dict(date='2026-09-04',version=VERSION,complete=True,signals={'US.A':{'sell':False}},candidates=[]),NOW)
                saved=engine.store.load()
                self.assertEqual([(q['code'],q['side'],q['exit_reason']) for q in saved['queue']],[('US.A','SELL','stop')])
                self.assertTrue(saved['paused'])
                self.assertFalse(any('inventory' in str(a).lower() for a in saved['alerts']))
                self.assertEqual(engines['option'].store.load()['positions']['US.O']['qty'],1)
                self.assertEqual(broker.calls,0)
                controls=root/'data/trading/simulation_controls.json'
                controls.write_text('{"paused":true,"stopped":false,"capital_migration_pending":true}')
                from trading.quote_feed import QuoteUnavailable
                native_submit=engine.submit
                def quote_failure_after_refresh(state,*args,**kwargs):
                    engine.reconcile(state,NOW)
                    raise QuoteUnavailable('Waiting for fresh book','connection_interrupted')
                engine.submit=quote_failure_after_refresh
                engine.drain(dict(complete=True,date='2026-09-04'),NOW)
                waiting=engine.store.load()
                self.assertEqual(len(waiting['queue']),1)
                self.assertEqual(waiting['queue'][0]['exit_reason'],'stop')
                self.assertFalse(waiting['orders'])
                self.assertEqual(broker.calls,0)
                engine.submit=native_submit
                broker.fail=True  # Accepted remotely, acknowledgement lost.
                engine.drain(dict(complete=True,date='2026-09-04'),NOW)
                uncertain=engine.store.load()
                self.assertEqual(next(iter(uncertain['orders'].values()))['status'],'UNKNOWN')
                self.assertEqual(broker.calls,1)
                tag=next(iter(uncertain['orders']))
                with self.assertRaisesRegex(SafetyError,'No active scoped exit intent'):
                    engine.b.place('US.A','SELL',1,10,tag)
                with self.assertRaisesRegex(SafetyError,'cannot mutate broker orders'):
                    engines['option'].b.place('US.O','SELL',1,10,'unowned')
                with self.assertRaisesRegex(SafetyError,'cannot mutate broker orders'):
                    engine.b.cancel('1')
                self.assertEqual(broker.calls,1)
                broker.fail=False
                engine.reconcile(uncertain,NOW)
                submitted=engine.store.load()
                self.assertEqual(broker.calls,1)
                order=next(iter(submitted['orders'].values()))
                self.assertEqual((order['side'],order['status'],order['exit_reason']),('SELL','SUBMITTED','stop'))
                broker.fill(1,10)
                engine.reconcile(submitted,NOW)
                self.assertEqual(submitted['positions']['US.A']['qty'],0)
                self.assertEqual(engines['option'].store.load()['positions']['US.O']['qty'],1)
                self.assertTrue(submitted['paused'])
                self.assertFalse(submitted['queue'])

    def test_scoped_exit_boundary_faults_never_reach_broker(self):
        import json
        from tests.test_swing import Broker
        from trading.simulation_reconcile import account_session
        for fault in ('stop','tamper','expired','missing_controls'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);broker=Broker();path=root/'stock.sqlite'
                db=Store(path);state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
                state['positions']={'US.A':dict(qty=1,kind='STOCK',multiplier=1,cost=10,realized=0,fees=0)}
                state['paused']=True;db.save(state);db.close();broker.pos={'US.A':1}
                archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
                with account_session(root,broker,{'stock':path},account='chosen',archive=archive,stock_exit_owner='stock') as (_,evidence,engines):
                    engine=engines['stock'];controls=root/'data/trading/simulation_controls.json'
                    controls.write_text(json.dumps(dict(paused=True,stopped=False)))
                    native_save=engine.store.save
                    def save_with_fault(state,event=None,detail=None):
                        result=native_save(state,event,detail)
                        if event=='order_intent':
                            if fault=='stop':controls.write_text(json.dumps(dict(paused=True,stopped=True)))
                            elif fault=='missing_controls':controls.unlink()
                            elif fault=='expired':evidence['query_started_at']='2000-01-01T00:00:00+00:00'
                            else:
                                changed=engine.store.load()
                                next(iter(changed['orders'].values()))['price']+=1
                                native_save(changed)
                        return result
                    engine.store.save=save_with_fault
                    tag=engine.submit(engine.store.load(),'US.A','SELL',1,10,NOW)
                    self.assertEqual(broker.calls,0)
                    self.assertEqual(engine.store.load()['orders'][tag]['status'],'UNKNOWN')
                    transport=engine.b
                    with self.assertRaisesRegex(SafetyError,'No active scoped exit intent'):
                        transport.place('US.A','SELL',1,10,tag)
                with self.assertRaisesRegex(SafetyError,'No active scoped exit intent'):
                    transport.place('US.A','SELL',1,10,tag)
                self.assertEqual(broker.calls,0)

    def test_peer_inventory_and_external_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            engines={};states={};stores=[]
            try:
                for owner,code in [('stock','US.A'),('option','US.O')]:
                    db=Store(Path(folder)/(owner+'.sqlite'));stores.append(db);e=Engine(Fake(),db,Config())
                    s=e.bind('chosen','SIMULATE',.6,NOW)
                    s['positions']={code:dict(qty=1,kind='STOCK',cost=10,multiplier=1)}
                    engines[owner]=e;states[owner]=s
                result=reconcile_engines(engines,states,'chosen',[],{'US.A':1,'US.O':1},NOW)
                self.assertEqual(len(result['positions']),2)
                self.assertTrue(all(not s['paused'] for s in states.values()))
                with self.assertRaises(SafetyError):reconcile_engines(engines,states,'chosen',[],{'US.A':1,'US.O':1,'US.X':1},NOW)
            finally:
                for db in stores:db.close()

    def test_fill_restart_after_second_owner_failure_never_double_books(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            engines={};states={};stores=[];rows=[]
            try:
                for owner,code in [('stock','US.A'),('option','US.O')]:
                    db=Store(Path(folder)/(owner+'.sqlite'));stores.append(db);e=Engine(Fake(),db,Config())
                    s=e.bind('chosen','SIMULATE',.6,NOW)
                    kind='OPTION' if owner=='option' else 'STOCK';mult=100 if kind=='OPTION' else 1
                    s['orders'][owner]=dict(id=owner,code=code,side='BUY',qty=2,status='SUBMITTED',kind=kind,multiplier=mult,booked_qty=0,booked_notional=0,booked_fee=0,fee_reserve=3,fee_final=False)
                    db.save(s);engines[owner]=e;states[owner]=s
                    rows.append(dict(id=owner,code=code,side='BUY',qty=2,status='FILLED_PART',filled=1,average=1,tag=owner))
                with patch.object(engines['option'],'reconcile',side_effect=RuntimeError('simulated interruption')):
                    with self.assertRaises(RuntimeError):reconcile_engines(engines,states,'chosen',rows,{'US.A':1,'US.O':1},NOW)
                first_cash=stores[0].load()['cash']
                states={k:e.store.load() for k,e in engines.items()}
                reconcile_engines(engines,states,'chosen',rows,{'US.A':1,'US.O':1},NOW)
                self.assertEqual(states['stock']['cash'],first_cash)
                self.assertEqual(states['option']['orders']['option']['booked_notional'],100)
                cash={k:s['cash'] for k,s in states.items()}
                reconcile_engines(engines,states,'chosen',rows,{'US.A':1,'US.O':1},NOW)
                self.assertEqual({k:s['cash'] for k,s in states.items()},cash)
                # Cumulative average changes on the second fill: book only the delta.
                for row in rows:row.update(status='FILLED_ALL',filled=2,average=1.5)
                states={k:e.store.load() for k,e in engines.items()}
                reconcile_engines(engines,states,'chosen',rows,{'US.A':2,'US.O':2},NOW)
                for owner,multiplier in [('stock',1),('option',100)]:
                    self.assertEqual(states[owner]['orders'][owner]['booked_notional'],3*multiplier)
                    self.assertEqual(states[owner]['cash'],cash[owner]-2*multiplier+2) # fee reserve 3 settles to actual 1
                final_cash={k:s['cash'] for k,s in states.items()}
                states={k:e.store.load() for k,e in engines.items()}
                reconcile_engines(engines,states,'chosen',rows,{'US.A':2,'US.O':2},NOW)
                self.assertEqual({k:s['cash'] for k,s in states.items()},final_cash)
            finally:
                for db in stores:db.close()

    def test_invalid_peer_fill_rejected_before_first_engine_books(self):
        from unittest.mock import Mock
        local=lambda oid,code:dict(id=oid,code=code,side='BUY',qty=2,booked_qty=0)
        states={k:dict(env='SIMULATE',account='a',baseline={},positions={},orders={k:local(k,c)}) for k,c in [('stock','US.A'),('option','US.O')]}
        engines={k:Mock() for k in states}
        rows=[dict(id=k,code=c,side='BUY',qty=2,filled=1,average=10,status='FILLED_PART') for k,c in [('stock','US.A'),('option','US.O')]]
        rows[1]['filled']=float('nan')
        with self.assertRaises(SafetyError):reconcile_engines(engines,states,'a',rows,{'US.A':1,'US.O':1},NOW)
        for engine in engines.values():engine.reconcile.assert_not_called()

    def test_account_context_holds_all_locks_through_caller_risk_checks(self):
        import fcntl,sqlite3
        from trading.simulation_reconcile import account_session
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);paths={k:root/(k+'.sqlite') for k in ('stock','option')}
            for path in paths.values():
                db=Store(path)
                Engine(Fake(),db,Config()).bind('chosen','SIMULATE',.6,NOW)
                db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            locks=[root/'data/trading/shared_admission.lock',*[Path(str(p)+'.lock') for p in paths.values()]]
            session_broker=Fake()
            with self.assertRaisesRegex(RuntimeError,'caller failed'):
                with account_session(root,session_broker,paths,account='chosen',archive=archive) as (states,evidence,engines):
                    self.assertEqual(set(states),set(paths))
                    for owner,engine in engines.items():
                        self.assertEqual(engine.store.load(),states[owner])
                        engine.store.event('session_readiness',{})
                        for mutation in ('place','cancel'):
                            with self.assertRaisesRegex(SafetyError,'cannot mutate broker orders'):
                                getattr(engine.b,mutation)
                        self.assertEqual(engine.b.positions(),{})
                    import json
                    controls=root/'data/trading/simulation_controls.json'
                    controls.write_text(json.dumps(dict(paused=True,stopped=False)))
                    self.assertTrue(engines['stock'].controls()['paused'])
                    controls.write_text(json.dumps(dict(paused=True,stopped=True)))
                    self.assertTrue(engines['option'].controls()['stopped'])
                    self.assertEqual(engines['stock'].news.path,root/'data/trading/news/latest.json')
                    self.assertEqual(evidence['current_orders'],0)
                    engines['stock']._independent_sizing=('stale',)
                    refreshed=engines['stock'].refresh_account()
                    self.assertEqual(refreshed['current_orders'],0)
                    self.assertFalse(hasattr(engines['stock'],'_independent_sizing'))
                    local=engines['stock'].store.load()
                    local_id=id(local)
                    engines['stock'].refresh_owned_state(local)
                    self.assertEqual(id(local),local_id)
                    self.assertEqual(local,states['stock'])
                    # Native strategy entry uses joint refresh without recursively
                    # invoking itself inside the per-owner accounting pass.
                    self.assertIs(engines['stock'].reconcile(local,NOW),local)
                    self.assertEqual(local,states['stock'])
                    local['stopped']=not local['stopped']
                    with self.assertRaisesRegex(SafetyError,'Owned state changed'):
                        engines['stock'].refresh_owned_state(local)
                    engines['stock'].store.save(local,'persisted_stop')
                    engines['stock'].reconcile(local,NOW)
                    self.assertTrue(local['stopped'])
                    self.assertTrue(states['stock']['stopped'])
                    # Aliasing the session dict must not authorize an unsaved edit.
                    states['stock']['stopped']=False
                    with self.assertRaisesRegex(SafetyError,'Owned state changed'):
                        engines['stock'].reconcile(states['stock'],NOW)
                    import json
                    raw=json.loads((root/'data/trading/joint_account_evidence.jsonl').read_text().splitlines()[-1])
                    self.assertEqual(raw['account'],'chosen')
                    self.assertEqual(raw['orders'],[])
                    self.assertEqual(raw['positions'],{})
                    for path in locks:
                        with path.open('a') as handle:
                            with self.assertRaises(BlockingIOError):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    from unittest.mock import patch
                    with patch.object(session_broker,'positions',side_effect=TimeoutError('refresh failed')):
                        with self.assertRaises(TimeoutError):engines['stock'].refresh_account()
                    self.assertEqual(evidence,{})
                    raise RuntimeError('caller failed')
            for engine in engines.values():
                with self.assertRaises(sqlite3.ProgrammingError):engine.store.load()
                with self.assertRaisesRegex(SafetyError,'session is closed'):engine.refresh_account()
            from unittest.mock import Mock
            broker=Fake();broker.positions=Mock(side_effect=TimeoutError('positions timeout'))
            with self.assertRaises(TimeoutError):
                with account_session(root,broker,paths,account='chosen',archive=archive):pass
            raw=json.loads((root/'data/trading/joint_account_evidence.jsonl').read_text().splitlines()[-1])
            self.assertEqual(raw['stage'],'orders_received')
            self.assertEqual(raw['orders'],[])
            self.assertNotIn('positions',raw)
            self.assertEqual(broker.calls,0)
            broker=Fake();broker.orders=Mock(return_value=[dict(id='unknown',average=float('nan'))])
            with self.assertRaises(SafetyError):
                with account_session(root,broker,paths,account='chosen',archive=archive):pass
            raw=json.loads((root/'data/trading/joint_account_evidence.jsonl').read_text().splitlines()[-1])
            self.assertEqual(raw['orders'][0]['average'],{'nonfinite_float':'nan'})
            self.assertEqual(broker.calls,0)
            # Even failed downstream risk checks must release every lock.
            for path in locks:
                with path.open('a') as handle:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_lost_ack_is_queried_and_recovered_without_resubmission(self):
        from trading.simulation_reconcile import reconciled_account
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';db=Store(path);broker=Fake()
            state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            state['orders']['durable']=dict(id=None,code='US.A',side='BUY',qty=1,status='UNKNOWN',
                booked_qty=0,booked_notional=0,booked_fee=0,fee_reserve=3,fee_final=False,kind='STOCK',multiplier=1)
            state['strategy_version']='swing_1_5_v1'
            state['orders']['durable'].update(created=NOW.isoformat(),stock_plan=dict(stop_distance=2,target_distance=4))
            db.save(state);db.close()
            row=dict(id='remote',tag='durable',code='US.A',side='BUY',qty=1,status='SUBMITTED',filled=0,average=0)
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            for remote in ([],[row,dict(row,id='duplicate')],[dict(row,code='US.X')]):
                broker.remote=remote
                with self.assertRaises(SafetyError):
                    with reconciled_account(root,broker,{'stock':path},account='chosen',archive=archive):pass
                db=Store(path);self.assertIsNone(db.load()['orders']['durable']['id']);db.close()
            row.update(status='FILLED_ALL',filled=1,average=10)
            broker.remote=[row];broker.pos={'US.A':1}
            with reconciled_account(root,broker,{'stock':path},account='chosen',archive=archive) as (states,_):
                self.assertEqual(states['stock']['orders']['durable']['id'],'remote')
                self.assertEqual(states['stock']['positions']['US.A']['swing_stop'],8)
                self.assertEqual(states['stock']['positions']['US.A']['swing_target'],14)
            db=Store(path);self.assertEqual(db.load()['orders']['durable']['id'],'remote');db.close()
            self.assertEqual(broker.calls,0)

    def test_joint_expiry_cancels_and_reconciles_without_news(self):
        from trading.simulation_reconcile import cancel_expired_locked
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';broker=Fake();db=Store(path)
            s=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            tag='owned';oid=broker.place('US.A','BUY',1,10,tag)
            s['orders'][tag]=dict(id=oid,code='US.A',side='BUY',qty=1,price=10,kind='STOCK',multiplier=1,
                created=NOW.isoformat(),status='SUBMITTED',booked_qty=0,booked_notional=0,
                booked_fee=0,fee_reserve=3,fee_final=False)
            s['paused']=True;db.save(s);db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            broker.cancel_fail=True
            with self.assertRaises(TimeoutError):
                cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive)
            self.assertEqual(broker.remote[0]['status'],'SUBMITTED')
            broker.cancel_fail=False
            result=cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive)
            self.assertEqual(result[0]['status'],'CANCELLED_ALL')
            self.assertEqual(broker.calls,1)
            db=Store(path)
            try:
                self.assertTrue(db.load()['paused'])
                self.assertEqual(db.load()['orders'][tag]['booked_qty'],0)
            finally:db.close()

    def test_uncertain_cancel_does_not_skip_other_verified_expired_orders(self):
        from trading.simulation_reconcile import cancel_expired_locked
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';broker=Fake();db=Store(path)
            state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            for tag,code in [('first','US.A'),('second','US.B')]:
                oid=broker.place(code,'BUY',1,10,tag)
                state['orders'][tag]=dict(id=oid,code=code,side='BUY',qty=1,price=10,kind='STOCK',multiplier=1,
                    created=NOW.isoformat(),status='SUBMITTED',booked_qty=0,booked_notional=0,
                    booked_fee=0,fee_reserve=3,fee_final=False)
            db.save(state);db.close();original=broker.cancel;attempted=[]
            def cancel(oid):
                attempted.append(oid)
                if oid=='1':raise TimeoutError('first unresolved')
                return original(oid)
            broker.cancel=cancel
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            with self.assertRaises(TimeoutError):
                cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive)
            self.assertEqual(attempted,['1','2'])
            self.assertEqual([x['status'] for x in broker.remote],['SUBMITTED','CANCELLED_ALL'])
            self.assertEqual(broker.calls,2)

    def test_control_maintenance_cancels_fresh_buy_preserves_safe_sell(self):
        import json
        from datetime import datetime,timezone
        from trading.simulation_reconcile import cancel_expired_locked
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';broker=Fake();db=Store(path)
            state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            for tag,side in [('entry','BUY'),('exit','SELL')]:
                oid=broker.place('US.A',side,1,10,tag)
                state['orders'][tag]=dict(id=oid,code='US.A',side=side,qty=1,price=10,kind='STOCK',multiplier=1,
                    created=datetime.now(timezone.utc).isoformat(),status='SUBMITTED',booked_qty=0,
                    booked_notional=0,booked_fee=0,fee_reserve=3,fee_final=False)
            db.save(state);db.close()
            controls=root/'data/trading/simulation_controls.json';controls.parent.mkdir(parents=True)
            controls.write_text(json.dumps(dict(paused=True,stopped=False)))
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            result=cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive,honor_controls=True)
            self.assertEqual([r['tag'] for r in result],['entry'])
            self.assertEqual(result[0]['reason'],'entry_paused')
            self.assertEqual(broker.remote[1]['status'],'SUBMITTED')
            controls.write_text(json.dumps(dict(paused=True,stopped=True)))
            result=cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive,honor_controls=True)
            self.assertEqual([r['tag'] for r in result],['exit'])
            self.assertEqual(result[0]['reason'],'stopped')
            self.assertEqual(broker.calls,2)

    def test_joint_cancel_preserves_unfilled_option_exit_alert(self):
        from trading.simulation_reconcile import cancel_expired_locked
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'option.sqlite';broker=Fake();db=Store(path)
            engine=Engine(broker,db,Config());state=engine.bind('chosen','SIMULATE',.6,NOW)
            for tag,side in [('entry','BUY'),('exit','SELL')]:
                oid=broker.place('US.O',side,1,1,tag)
                state['orders'][tag]=dict(id=oid,code='US.O',side=side,qty=1,price=1,kind='OPTION',multiplier=100,
                    created=NOW.isoformat(),status='SUBMITTED',booked_qty=0,booked_notional=0,
                    booked_fee=0,fee_reserve=5,fee_final=False)
                if side=='BUY':
                    broker.fill(1,price=1);engine.reconcile(state,NOW)
            db.save(state);db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            result=cancel_expired_locked(root,broker,{'option':path},account='chosen',archive=archive)
            self.assertEqual(result[0]['status'],'CANCELLED_ALL')
            db=Store(path)
            try:
                saved=db.load()
                self.assertTrue(saved['paused'])
                self.assertIn('Option exit unfilled: manual action required US.O',saved['alerts'])
                self.assertEqual(saved['positions']['US.O']['qty'],1)
            finally:db.close()
            self.assertEqual(broker.calls,2)

    def test_news_failure_does_not_block_other_expired_cancellation(self):
        from unittest.mock import patch
        from datetime import datetime,timezone
        from trading.simulation_reconcile import cancel_expired_locked
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';broker=Fake();db=Store(path)
            state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            for tag,code,created in [('fresh','US.A',datetime.now(timezone.utc)),('expired','US.B',NOW)]:
                oid=broker.place(code,'BUY',1,10,tag)
                state['orders'][tag]=dict(id=oid,code=code,side='BUY',qty=1,price=10,kind='STOCK',multiplier=1,
                    created=created.isoformat(),status='SUBMITTED',booked_qty=0,booked_notional=0,
                    booked_fee=0,fee_reserve=3,fee_final=False)
            db.save(state);db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            def unavailable(*args):
                # A slow/failed news source must be consulted only after the
                # already-expired order has been cancelled and reconciled.
                self.assertEqual(broker.remote[1]['status'],'CANCELLED_ALL')
                raise RuntimeError('news unavailable')
            with patch('trading.news.NewsGuard.__call__',side_effect=unavailable) as news:
                with self.assertRaises(RuntimeError):
                    cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive,honor_news=True)
                self.assertEqual(news.call_count,1)
            self.assertEqual([r['status'] for r in broker.remote],['SUBMITTED','CANCELLED_ALL'])
            with patch('trading.news.NewsGuard.__call__',return_value=dict(multiplier=0,event_ids=['bad'],reasons=['blocked'])):
                result=cancel_expired_locked(root,broker,{'stock':path},account='chosen',archive=archive,honor_news=True)
            self.assertEqual([r['tag'] for r in result],['fresh'])
            self.assertEqual(result[0]['reason'],'news_blocked')
            import json,sqlite3
            with sqlite3.connect(path) as connection:
                detail=json.loads(connection.execute("SELECT body FROM events WHERE kind='cancel_intent' ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(detail['reason'],'news_blocked')
            self.assertEqual(detail['news_decision']['event_ids'],['bad'])
            db=Store(path)
            try:self.assertEqual(db.load()['news_status']['US.A']['event_ids'],['bad'])
            finally:db.close()
            self.assertEqual(broker.calls,2)

    def test_loss_maintenance_services_orders_before_missing_hk_evidence(self):
        from trading.simulation_admission import observe_locked_us_loss
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'stock.sqlite';broker=Fake();db=Store(path)
            state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
            oid=broker.place('US.A','BUY',1,10,'entry')
            state['orders']['entry']=dict(id=oid,code='US.A',side='BUY',qty=1,price=10,kind='STOCK',multiplier=1,
                created=NOW.isoformat(),status='SUBMITTED',booked_qty=0,booked_notional=0,
                booked_fee=0,fee_reserve=3,fee_final=False)
            state['paused']=True;db.save(state);db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            with self.assertRaises(FileNotFoundError):
                observe_locked_us_loss(root,broker,{'stock':path},account='chosen',hk_account='hk',
                    portfolio='p',archive=archive,risk_path=root/'risk.sqlite',service_orders=True)
            self.assertEqual(broker.remote[0]['status'],'CANCELLED_ALL')
            self.assertEqual(broker.calls,1)
            db=Store(path)
            try:
                self.assertTrue(db.load()['paused'])
                self.assertEqual(db.load()['orders']['entry']['status'],'CANCELLED_ALL')
            finally:db.close()
            self.assertFalse((root/'risk.sqlite').exists())

class ScopedOptionExit(unittest.TestCase):
    def test_paused_owned_option_exit_keeps_peer_stock(self):
        from tests.test_option_transport import Broker
        from trading.simulation_reconcile import account_session
        broker=Broker();contract=dict(code='US.O',standard=True,multiplier=100,expiry='2026-10-23',underlying='US.A',right='CALL')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);paths={k:root/(k+'.sqlite') for k in ('stock','option')}
            for owner,path in paths.items():
                db=Store(path);state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
                kind='OPTION' if owner=='option' else 'STOCK';code='US.O' if owner=='option' else 'US.A'
                state.update(paused=True,probe_contract=contract,probe_code='US.O',probe_phase='sell',purpose='option_transport_probe')
                state['positions']={code:dict(qty=1,kind=kind,multiplier=100 if kind=='OPTION' else 1,cost=100 if kind=='OPTION' else 10,realized=0,fees=0)}
                db.save(state);db.close()
            broker.pos={'US.A':1,'US.O':1}
            with account_session(root,broker,paths,account='chosen',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08'),option_exit_owner='option') as (_,__,engines):
                (root/'data/trading/simulation_controls.json').write_text('{"paused":true,"stopped":false,"capital_migration_pending":true}')
                engine=engines['option'];state=engine.store.load()
                with self.assertRaisesRegex(SafetyError,'Unverified owned option'):
                    engine.submit(state,'US.O','SELL',1,.99,NOW,'OPTION',dict(contract,right='PUT'))
                self.assertEqual(broker.calls,0)
                from trading.option_transport import submit_confirmed_option_exit
                result=submit_confirmed_option_exit(engine,NOW)
                self.assertTrue(result['acknowledged'])
                tag=result['tag']
                with self.assertRaisesRegex(SafetyError,'Confirmed option sell phase'):
                    submit_confirmed_option_exit(engine,NOW)
                self.assertEqual(broker.calls,1)
                with self.assertRaisesRegex(SafetyError,'No active scoped exit intent'):engine.b.place('US.O','SELL',1,.99,tag)
                broker.fill(1,.99);state=engine.store.load();engine.reconcile(state,NOW)
                self.assertEqual(state['positions']['US.O']['qty'],0)
                self.assertEqual(engines['stock'].store.load()['positions']['US.A']['qty'],1)
                self.assertTrue(state['paused'])

    def test_lost_exit_ack_recovers_without_second_submission(self):
        from tests.test_option_transport import Broker
        from trading.simulation_reconcile import account_session
        broker=Broker();contract=dict(code='US.O',standard=True,multiplier=100,expiry='2026-10-23',underlying='US.A',right='CALL')
        original_place=broker.place
        def lost_ack(*args,**kwargs):
            original_place(*args,**kwargs)
            raise TimeoutError('Acknowledgement lost after broker accepted')
        broker.place=lost_ack
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);paths={k:root/(k+'.sqlite') for k in ('stock','option')}
            for owner,path in paths.items():
                db=Store(path);state=Engine(broker,db,Config()).bind('chosen','SIMULATE',.6,NOW)
                kind='OPTION' if owner=='option' else 'STOCK';code='US.O' if owner=='option' else 'US.A'
                state.update(paused=True,probe_contract=contract,probe_code='US.O',probe_phase='sell',purpose='option_transport_probe')
                state['positions']={code:dict(qty=1,kind=kind,multiplier=100 if kind=='OPTION' else 1,cost=100 if kind=='OPTION' else 10,realized=0,fees=0)}
                db.save(state);db.close()
            broker.pos={'US.A':1,'US.O':1}
            with account_session(root,broker,paths,account='chosen',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08'),option_exit_owner='option') as (_,__,engines):
                (root/'data/trading/simulation_controls.json').write_text('{"paused":true,"stopped":false,"capital_migration_pending":true}')
                engine=engines['option'];state=engine.store.load()
                with self.assertRaisesRegex(SafetyError,'Unverified owned option'):
                    engine.submit(state,'US.O','SELL',1,.99,NOW,'OPTION',dict(contract,right='PUT'))
                self.assertEqual(broker.calls,0)
                from trading.option_transport import submit_confirmed_option_exit
                result=submit_confirmed_option_exit(engine,NOW)
                self.assertFalse(result['acknowledged'])
                self.assertEqual(result['order_status'],'UNKNOWN')
                tag=result['tag']
                with self.assertRaisesRegex(SafetyError,'Confirmed option sell phase'):
                    submit_confirmed_option_exit(engine,NOW)
                self.assertEqual(broker.calls,1)
                with self.assertRaisesRegex(SafetyError,'No active scoped exit intent'):engine.b.place('US.O','SELL',1,.99,tag)
                broker.fill(1,.99);state=engine.store.load();engine.reconcile(state,NOW)
                self.assertEqual(state['positions']['US.O']['qty'],0)
                self.assertEqual(engines['stock'].store.load()['positions']['US.A']['qty'],1)
                self.assertTrue(state['paused'])
                self.assertEqual(broker.calls,1)
                self.assertEqual(state['orders'][tag]['booked_qty'],1)
                self.assertIsNotNone(state['orders'][tag]['id'])

    def test_scoped_independent_entry_rejects_then_sends_once_and_recovers(self):
        import json
        from trading.simulation_reconcile import account_session
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'data/trading';base.mkdir(parents=True)
            (base/'simulation_controls.json').write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
            broker=Fake();paths={k:root/(k+'.sqlite') for k in ('US_STOCK','US_OPTION')}
            cfg=Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500,shared_capital=True,allocation=1)
            for owner,path in paths.items():
                db=Store(path);state=Engine(broker,db,cfg).bind('chosen','SIMULATE',.6,NOW)
                state.update(capital_mode='independent_sleeves_v1',sleeve=owner,shared_capital=False,portfolio_id='p')
                db.save(state);db.close()
            archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start='2026-09-08')
            allowed=[False];checks=[]
            def validate(states,engines,evidence,state,order):
                checks.append(order['qty']);return allowed[0]
            with account_session(root,broker,paths,account='chosen',archive=archive,entry_owner='US_STOCK',entry_validator=validate) as (_,__,engines):
                engine=engines['US_STOCK'];engine.news=lambda c,n:dict(multiplier=1)
                state=engine.store.load()
                with self.assertRaisesRegex(SafetyError,'admission rejected'):
                    engine.submit(state,'US.A','BUY',1,10.01,NOW)
                self.assertFalse(engine.store.load()['orders']);self.assertEqual(broker.calls,0)
                allowed[0]=True;broker.fail=True
                tag=engine.submit(engine.store.load(),'US.A','BUY',1,10.01,NOW)
                state=engine.store.load();self.assertEqual(state['orders'][tag]['status'],'UNKNOWN')
                self.assertEqual(broker.calls,1)
                broker.fail=False;engine.reconcile(state,NOW)
                self.assertEqual(state['orders'][tag]['status'],'SUBMITTED');self.assertEqual(broker.calls,1)
                escaped=engine.b
            with self.assertRaises(SafetyError):escaped.place('US.A','BUY',1,10.01,tag)
            self.assertEqual(broker.calls,1)
