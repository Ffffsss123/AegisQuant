import unittest,tempfile
from pathlib import Path
from trading.simulation_admission import check_entry
from trading.simulation_loss_latch import initialize
from trading.policy import SafetyError

class Admission(unittest.TestCase):
    def test_local_cash_slot_pause_and_persistent_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            risk=Path(folder)/'risk.sqlite';initialize(risk,'p',inherited_stop=False)
            sleeves={k:dict(initial=500,cash=500,equity=500,deployed=0,reserved=0,exit_fees=0) for k in ('US_STOCK','US_OPTION','HK')}
            args=dict(risk_path=risk,portfolio='p',sleeves=sleeves,slots=[],controls=dict(paused=False,stopped=False,reset_pending=False),sleeve='US_OPTION',category='OPTION',required_usd=100,env='SIMULATE')
            self.assertEqual(check_entry(**args)['available_usd'],500)
            sleeves['US_OPTION']['cash']=99
            with self.assertRaises(SafetyError):check_entry(**args)
            sleeves['US_OPTION']['cash']=500
            args['slots']=[dict(category='OPTION',code=c,owner='options',account='a',state='ENTRY_PENDING') for c in ('US.A','HK.B')]
            with self.assertRaises(SafetyError):check_entry(**args)
            args['slots']=[];args['controls']['paused']=True;sleeves['HK']['equity']=400
            with self.assertRaises(SafetyError):check_entry(**args)
            args['controls']['paused']=False;sleeves['HK']['equity']=500
            with self.assertRaises(SafetyError):check_entry(**args)

    def test_reconciled_inputs_keep_peer_pause_and_local_cash(self):
        from datetime import datetime,timezone
        from trading.simulation_admission import check_us_entry
        now=datetime.now(timezone.utc)
        states={name:dict(env='SIMULATE',account='us',initial=500,cash=500,
            budget_currency='USD',positions={},orders={},paused=False,stopped=False,
            capital_mode='independent_sleeves_v1',sleeve=name,portfolio_id='p',shared_capital=False)
            for name in ('US_STOCK','US_OPTION')}
        hk=dict(as_of=now.isoformat(),account='hk',portfolio_id='p',env='SIMULATE',currency='USD',
            reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,
            committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
        with tempfile.TemporaryDirectory() as folder:
            risk=Path(folder)/'risk.sqlite';initialize(risk,'p',inherited_stop=False)
            from trading.hk_reservations import Reservations
            hk_path=Path(folder)/'hk.sqlite'
            Reservations(hk_path,'p').close()
            args=dict(states=states,books={},hk_snapshot=hk,hk_reservations_path=hk_path,account='us',hk_account='hk',
                portfolio='p',now=now,risk_path=risk,controls=dict(paused=False,stopped=False,reset_pending=False),
                sleeve='US_OPTION',required_usd=10)
            self.assertEqual(check_us_entry(**args)['available_usd'],500)
            states['US_STOCK']['paused']=True
            with self.assertRaises(SafetyError):check_us_entry(**args)
            states['US_STOCK']['paused']=False
            states['US_OPTION']['cash']=405
            args['required_usd']=410
            with self.assertRaises(SafetyError):check_us_entry(**args)
            states['US_OPTION']['shared_capital']=True
            with self.assertRaises(SafetyError):check_us_entry(**args)

            # A valid but paused HK snapshot must still persist a portfolio loss.
            states['US_OPTION'].update(shared_capital=False,cash=500)
            args['required_usd']=10
            hk.update(entry_block=True,realized_net_usd=-100)
            states['US_STOCK']['orders']['pending']=dict(id='known',code='US.A',kind='STOCK',
                side='BUY',status='SUBMITTED',qty=1,booked_qty=0,price=10,multiplier=1,
                fee_reserve=3,booked_fee=0,fee_final=False)
            with self.assertRaisesRegex(SafetyError,'Unresolved sleeve orders'):check_us_entry(**args)
            states['US_STOCK']['orders'].clear()
            hk.update(entry_block=False,realized_net_usd=0)
            with self.assertRaisesRegex(SafetyError,'Combined simulation loss stop'):check_us_entry(**args)

    def test_capital_flag_alone_cannot_submit_through_legacy_engine(self):
        from trading.engine import Engine
        from trading.store import Store
        from trading.policy import Config
        from tests.test_trading import Fake,NOW
        with tempfile.TemporaryDirectory() as folder:
            db=Store(Path(folder)/'paper.sqlite');broker=Fake()
            try:
                engine=Engine(broker,db,Config())
                state=engine.bind('chosen','SIMULATE',.6,NOW)
                state['capital_mode']='independent_sleeves_v1'
                with self.assertRaisesRegex(SafetyError,'submission path is not enabled'):
                    engine.submit(state,'US.A','BUY',1,10,NOW)
                self.assertEqual(broker.calls,0)
                self.assertEqual(state['orders'],{})
            finally:db.close()

    def test_locked_account_to_admission_with_real_databases(self):
        import json,fcntl
        from datetime import datetime,timezone
        from trading.engine import Engine
        from trading.store import Store
        from trading.policy import Config
        from trading.hk_reservations import Reservations
        from trading.simulation_admission import admitted_us_entry
        from tests.test_trading import Fake
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data/trading';base.mkdir(parents=True)
            paths={name:base/(name+'.sqlite') for name in ('US_STOCK','US_OPTION')}
            broker=Fake()
            for name,path in paths.items():
                db=Store(path)
                Engine(broker,db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind(
                    'chosen','SIMULATE',.6,now,extra=dict(capital_mode='independent_sleeves_v1',
                    sleeve=name,portfolio_id='p',shared_capital=False))
                db.close()
            Reservations(base/'hk_reservations.sqlite','p').close()
            risk=base/'risk.sqlite';initialize(risk,'p',inherited_stop=False)
            hk=dict(as_of=now.isoformat(),account='hk',portfolio_id='p',env='SIMULATE',currency='USD',
                reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,
                committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            control=base/'simulation_controls.json'
            control.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
            args=dict(account='chosen',hk_account='hk',portfolio='p',risk_path=risk,sleeve='US_OPTION',required_usd=100,
                archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start=now.date().isoformat()))
            with admitted_us_entry(root,broker,paths,**args) as (states,result):
                self.assertEqual(result['available_usd'],500)
                import sqlite3
                conn=sqlite3.connect(paths['US_OPTION'])
                try:
                    event=conn.execute("SELECT body FROM events WHERE kind='independent_admission_checked' ORDER BY id DESC LIMIT 1").fetchone()
                    self.assertEqual(json.loads(event[0])['submission_enabled'],False)
                finally:conn.close()
                for path in [base/'shared_admission.lock',*[Path(str(p)+'.lock') for p in paths.values()]]:
                    with path.open('a') as handle:
                        with self.assertRaises(BlockingIOError):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Exercise option preparation through the actual admission session,
            # SQLite ledgers and loss latch; only market contract data is stubbed.
            from trading.option_transport import prepare_independent_contract
            from unittest.mock import patch
            preparation_args={k:v for k,v in args.items() if k!='sleeve'}
            def verify_prepared(*a,**kw):
                self.assertEqual(kw['available_cash'],100)
                for path in [base/'shared_admission.lock',*[Path(str(p)+'.lock') for p in paths.values()]]:
                    with path.open('a') as handle:
                        with self.assertRaises(BlockingIOError):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return dict(code='US.FIXTURE',underlying='US.TEST')
            with patch('trading.news.NewsGuard',return_value=lambda c,n:dict(multiplier=1,global_block=False)), patch('trading.option_transport.validate_contract',side_effect=verify_prepared) as validate:
                prepared=prepare_independent_contract(root,broker,paths,contract={'underlying':'US.TEST'},**preparation_args)
                self.assertFalse(prepared['submission_enabled'])
                self.assertEqual(prepared['admission']['available_usd'],500)
                control.write_text(json.dumps(dict(paused=True,stopped=False,reset_pending=False)))
                with self.assertRaisesRegex(SafetyError,'paused'):
                    prepare_independent_contract(root,broker,paths,contract={'underlying':'US.TEST'},**preparation_args)
                self.assertEqual(validate.call_count,1)
            control.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
            # Existing peer inventory flows through real reconciliation and bid valuation.
            for name,code,kind,mult,cost in [('US_STOCK','US.A','STOCK',1,10),('US_OPTION','US.O','OPTION',100,100)]:
                db=Store(paths[name]);state=db.load();state['cash']=500-cost
                state['positions']={code:dict(qty=1,kind=kind,multiplier=mult,cost=cost)}
                db.save(state);db.close();broker.pos[code]=1
            queried=[]
            def fresh_book(code):
                queried.append(code);stamp=datetime.now(timezone.utc).isoformat()
                return dict(bid=10 if code=='US.A' else 1,ask=12 if code=='US.A' else 1.01,bid_time=stamp,ask_time=stamp)
            broker.book=fresh_book
            with admitted_us_entry(root,broker,paths,**args) as (_,result):
                self.assertEqual(result['available_usd'],395)
                self.assertEqual(result['counts']['US_STOCK'],1)
                self.assertEqual(result['counts']['OPTION'],1)
            self.assertEqual(set(queried),{'US.A','US.O'})
            from trading.swing_engine import SwingEngine
            swing=SwingEngine.__new__(SwingEngine);swing.b=broker
            stock_args=dict(args,sleeve='US_STOCK')
            with admitted_us_entry(root,broker,paths,sizing_engine=swing,**stock_args) as (states,result):
                stock=states['US_STOCK'];stock['option_budget_usd']=500
                self.assertEqual(swing.stock_cash(stock),487)
                self.assertEqual(swing.combined_equity(stock,now),500-float(result['combined_loss']))
                with self.assertRaises(SafetyError):swing.stock_cash(dict(stock))
            with self.assertRaises(SafetyError):swing.stock_cash(stock)
            self.assertFalse(hasattr(swing,'_independent_sizing'))
            db=Store(paths['US_STOCK']);stock=db.load();stock['strategy_version']='swing_1_5_v1';db.save(stock);db.close()
            from trading.simulation_admission import admitted_us_session
            market=base/'market';market.mkdir()
            broker.completed_session=lambda stamp:now.date().isoformat()
            (market/'scan.json').write_text(json.dumps(dict(date=now.date().isoformat(),complete=True,generated_at='source')))
            (market/'short_scan.json').write_text(json.dumps(dict(date=now.date().isoformat(),complete=True,
                source_generated_at='source',version='swing_1_5_v1',signals={'US.A':{}},candidates=[])))
            with admitted_us_session(root,broker,paths,**stock_args) as (states,result,engines):
                native=engines['US_STOCK']
                self.assertIsInstance(native,SwingEngine)
                self.assertEqual(native.active_scan['source_generated_at'],'source')
                self.assertEqual(native.news.path,base/'news/latest.json')
                self.assertFalse(native.controls()['paused'])
                control.write_text(json.dumps(dict(paused=True,stopped=False,reset_pending=False)))
                self.assertTrue(native.controls()['paused'])
                control.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
                self.assertEqual(native.stock_cash(states['US_STOCK']),487)
                self.assertEqual(native.store.load(),states['US_STOCK'])
            self.assertFalse(hasattr(native,'_independent_sizing'))
            scan_path=market/'short_scan.json';saved=scan_path.read_text()
            mismatched=json.loads(saved);mismatched['source_generated_at']='older source';scan_path.write_text(json.dumps(mismatched))
            with self.assertRaisesRegex(SafetyError,'matching swing scan'):
                with admitted_us_session(root,broker,paths,**stock_args):pass
            conn=sqlite3.connect(paths['US_STOCK'])
            try:
                row=conn.execute("SELECT kind,body FROM events WHERE kind LIKE 'independent_admission_%' ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(row[0],'independent_admission_rejected')
                self.assertIn('matching swing scan',json.loads(row[1])['reason'])
            finally:conn.close()
            scan_path.write_text(saved)
            from datetime import timedelta
            def stale_book(code):
                row=fresh_book(code);row['bid_time']=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat();return row
            broker.book=stale_book
            with self.assertRaises(SafetyError):
                with admitted_us_entry(root,broker,paths,**args):pass
            def failed_book(code):raise TimeoutError('quote transport failed')
            broker.book=failed_book
            with self.assertRaises(TimeoutError):
                with admitted_us_entry(root,broker,paths,**args):pass
            conn=sqlite3.connect(paths['US_OPTION'])
            try:
                row=conn.execute("SELECT body FROM events WHERE kind='independent_admission_rejected' ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual(json.loads(row[0])['reason'],'quote transport failed')
            finally:conn.close()
            broker.book=fresh_book
            control.write_text(json.dumps(dict(paused=True,stopped=False,reset_pending=False)))
            with self.assertRaises(SafetyError):
                with admitted_us_entry(root,broker,paths,**args):pass
            from trading.simulation_admission import observe_locked_us_loss
            hk.update(as_of=datetime.now(timezone.utc).isoformat(),realized_net_usd=-150,entry_block=True)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            maintenance={k:v for k,v in args.items() if k not in ('sleeve','required_usd')}
            observed=observe_locked_us_loss(root,broker,paths,**maintenance)
            self.assertTrue(observed['loss_latched'])
            (base/'hk_risk_snapshot.json').unlink()
            def hk_provider():
                with (base/'shared_admission.lock').open('a') as handle:
                    with self.assertRaises(BlockingIOError):fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return dict(account='hk',us_account='chosen',portfolio_id='p',env='SIMULATE',
                    currency='USD',budget_usd=500,inventory_reconciled=True,loss_observation_only=True,
                    orders_enabled=False,as_of=datetime.now(timezone.utc).isoformat(),equity_usd='350')
            pending=observe_locked_us_loss(root,broker,paths,hk_loss_provider=hk_provider,**maintenance)
            self.assertTrue(pending['loss_latched'])
            self.assertTrue(pending['entry_block'])
            self.assertTrue(all(value==0 for value in pending['available'].values()))

            # Cancellation failure cannot suppress independently provable loss.
            from unittest.mock import patch
            from trading.simulation_loss_latch import retain_loss_stop
            isolated_risk=root/'cancel_failure_risk.sqlite'
            initialize(isolated_risk,'p',inherited_stop=False)
            failing_maintenance=dict(maintenance,risk_path=isolated_risk)
            with patch('trading.simulation_reconcile.cancel_expired_locked',side_effect=TimeoutError('cancel uncertain')):
                with self.assertRaisesRegex(TimeoutError,'cancel uncertain'):
                    observe_locked_us_loss(root,broker,paths,hk_loss_provider=hk_provider,
                        service_orders=True,**failing_maintenance)
            self.assertTrue(retain_loss_stop(isolated_risk,'p',env='SIMULATE',triggered=False))
            self.assertTrue(json.loads(control.read_text())['paused'])
            self.assertEqual(broker.calls,0)
