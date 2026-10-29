import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from trading.engine import Engine
from trading.store import Store
from trading.policy import Config,SafetyError
from trading.hk_reservations import Reservations
from trading.simulation_loss_latch import initialize
from trading.independent_execution import submit_entry
from tests.test_trading import Fake

class Execution(unittest.TestCase):
    def test_real_admission_databases_pause_loss_and_one_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data/trading';(base/'market').mkdir(parents=True);(base/'news').mkdir()
            now=datetime.now(timezone.utc);broker=Fake();broker.now=lambda:datetime.now(timezone.utc)
            broker.book=lambda code:dict(bid=.99,ask=1,bid_time=broker.now().isoformat(),ask_time=broker.now().isoformat())
            paths={name:base/(name+'.sqlite') for name in ('US_STOCK','US_OPTION')}
            for name,path in paths.items():
                db=Store(path);Engine(broker,db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind('chosen','SIMULATE',.6,now,extra=dict(capital_mode='independent_sleeves_v1',sleeve=name,portfolio_id='p',shared_capital=False));db.close()
            Reservations(base/'hk_reservations.sqlite','p').close();initialize(base/'simulation_loss_latch.sqlite','p',inherited_stop=False)
            hk=dict(as_of=now.isoformat(),account='hk',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            controls=base/'simulation_controls.json';controls.write_text(json.dumps(dict(paused=True,stopped=False,reset_pending=False)))
            (base/'market/scan.json').write_text(json.dumps(dict(date='2026-09-04',complete=True,signals={})))
            (base/'news/latest.json').write_text(json.dumps(dict(version=3,collected_at=now.isoformat(),required_sources=['source'],health={k:dict(ok=True,checked_at=now.isoformat()) for k in ('source','US.A')},events=[])))
            contract=dict(code='US.O',underlying='US.A',right='CALL',expiry='2026-10-23',standard=True,multiplier=100)
            args=dict(account='chosen',hk_account='hk',portfolio='p',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start=now.date().isoformat()),sleeve='US_OPTION',code='US.O',qty=1,price=1,refresh_hk=lambda:dict(status='reconciled'),contract=contract)
            with patch('trading.option_transport.validate_contract',return_value=contract) as validate:
                with self.assertRaises(SafetyError):submit_entry(root,broker,paths,**args)
                validate.assert_not_called();self.assertEqual(broker.calls,0)
                controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
                result=submit_entry(root,broker,paths,**args)
                self.assertTrue(result['acknowledged']);self.assertEqual(broker.calls,1)
                with self.assertRaises(SafetyError):submit_entry(root,broker,paths,**args)
                self.assertEqual(broker.calls,1)
                db=Store(paths['US_STOCK'])
                try:self.assertEqual(db.load()['cash'],500);self.assertFalse(db.load()['orders'])
                finally:db.close()

    def test_option_cycle_cancel_buy_sell_and_completion(self):
        from trading.independent_option_cycle import run_once
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data/trading';(base/'market').mkdir(parents=True);(base/'news').mkdir()
            now=datetime.now(timezone.utc);broker=Fake();broker.now=lambda:datetime.now(timezone.utc)
            broker.book=lambda code:dict(bid=.99,ask=1,bid_time=broker.now().isoformat(),ask_time=broker.now().isoformat())
            paths={name:base/(name+'.sqlite') for name in ('US_STOCK','US_OPTION')}
            for name,path in paths.items():
                db=Store(path);Engine(broker,db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind('chosen','SIMULATE',.6,now,extra=dict(capital_mode='independent_sleeves_v1',sleeve=name,portfolio_id='p',shared_capital=False,purpose='option_transport_probe' if name=='US_OPTION' else 'strategy_simulation',probe_phase='cancel_test'));db.close()
            Reservations(base/'hk_reservations.sqlite','p').close();initialize(base/'simulation_loss_latch.sqlite','p',inherited_stop=False)
            hk=dict(as_of=now.isoformat(),account='hk',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            controls=base/'simulation_controls.json';controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
            (base/'market/scan.json').write_text(json.dumps(dict(date='2026-09-04',complete=True,signals={})))
            (base/'news/latest.json').write_text(json.dumps(dict(version=3,collected_at=now.isoformat(),required_sources=['source'],health={k:dict(ok=True,checked_at=now.isoformat()) for k in ('source','US.A')},events=[])))
            contract=dict(code='US.O',underlying='US.A',right='CALL',expiry='2026-10-23',standard=True,multiplier=100,tick_size=.01)
            (base/'options_broker_candidate.json').write_text(json.dumps(dict(created_at=now.isoformat(),contract=contract)))
            args=dict(account='chosen',hk_account='hk',portfolio='p',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start=now.date().isoformat()),refresh_hk=lambda:dict(status='reconciled'))
            with patch('trading.option_transport.validate_contract',return_value=contract):
                controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False,require_news_opportunity=True)))
                with self.assertRaisesRegex(SafetyError,'positive news opportunity'):
                    run_once(root,broker,paths,**args)
                db=Store(paths['US_OPTION']);rejected=db.load();db.close()
                self.assertEqual(broker.calls,0);self.assertFalse(rejected['orders'])
                self.assertIsNone(rejected.get('probe_code'));self.assertIsNone(rejected.get('probe_contract'))
                self.assertEqual(rejected['probe_phase'],'cancel_test')
                controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
                original_place=broker.place
                def assert_durable_binding(code,side,qty,price,tag):
                    db=Store(paths['US_OPTION']);durable=db.load();db.close()
                    self.assertEqual(durable['probe_code'],code)
                    self.assertEqual(durable['probe_contract'],contract)
                    self.assertEqual(durable['probe_phase'],'cancel_wait')
                    self.assertEqual(durable['orders'][tag]['status'],'INTENT')
                    return original_place(code,side,qty,price,tag)
                with patch.object(broker,'place',side_effect=assert_durable_binding):
                    self.assertTrue(run_once(root,broker,paths,**args)['acknowledged']);self.assertEqual(broker.calls,1)
                db=Store(paths['US_OPTION']);state=db.load()
                next(iter(state['orders'].values()))['created']=(now-timedelta(seconds=61)).isoformat()
                db.save(state);db.close()
                self.assertTrue(run_once(root,broker,paths,**args)['acknowledged']);self.assertEqual(broker.calls,2)
                self.assertEqual(broker.remote[0]['status'],'CANCELLED_ALL')
                broker.fill(1,1)
                self.assertTrue(run_once(root,broker,paths,**args)['acknowledged']);self.assertEqual(broker.calls,3)
                self.assertEqual(broker.remote[-1]['side'],'SELL')
                broker.fill(1,.99)
                self.assertEqual(run_once(root,broker,paths,**args)['status'],'transport_passed')
                self.assertEqual(run_once(root,broker,paths,**args)['status'],'transport_passed')
                self.assertEqual(broker.calls,3)
                db=Store(paths['US_OPTION']);completed=db.load();db.close()
                self.assertEqual({o['execution_purpose'] for o in completed['orders'].values()},{'option_transport_probe'})

    def test_hk_entry_requires_owned_lock_news_and_separate_budget(self):
        from trading.hk_independent_admission import reserve_buy
        from trading.shared_capital import admission_lock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data/trading';(base/'market').mkdir(parents=True);(base/'news').mkdir()
            now=datetime.now(timezone.utc);broker=Fake();broker.now=lambda:datetime.now(timezone.utc)
            broker.book=lambda code:dict(bid=.99,ask=1,bid_time=broker.now().isoformat(),ask_time=broker.now().isoformat())
            paths={name:base/(name+'.sqlite') for name in ('US_STOCK','US_OPTION')}
            for name,path in paths.items():
                db=Store(path);Engine(broker,db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind('chosen','SIMULATE',.6,now,extra=dict(capital_mode='independent_sleeves_v1',sleeve=name,portfolio_id='p',shared_capital=False,hk_account='8653'));db.close()
            Reservations(base/'hk_reservations.sqlite','p').close();initialize(base/'simulation_loss_latch.sqlite','p',inherited_stop=False)
            hk=dict(as_of=now.isoformat(),account='8653',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            controls=base/'simulation_controls.json';controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False,require_news_opportunity=True)))
            (base/'market/scan.json').write_text(json.dumps(dict(date='2026-09-04',complete=True,signals={})))
            (base/'news/latest.json').write_text(json.dumps(dict(version=3,collected_at=now.isoformat(),required_sources=['source'],health={k:dict(ok=True,checked_at=now.isoformat()) for k in ('source','HK.00001')},events=[])))
            fx=dict(kind='fixed_simulation_accounting',env='SIMULATE',portfolio_id='p',account='8653',bound_at=now.isoformat(),hkd_per_usd=8,reference=dict(source='fixture',verified=True,as_of=now.isoformat(),hkd_per_usd=8))
            (base/'hk_fx_binding.json').write_text(json.dumps(fx))
            reservations=Reservations(base/'hk_reservations.sqlite','p')
            request=dict(env='SIMULATE',account='8653',side='BUY',code='HK.00001',tag='HK-SIM-first',qty=100,price=10,notional_hkd='1000')
            args=dict(account='chosen',hk_account='8653',portfolio='p',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start=now.date().isoformat()),reservations=reservations,hk_cash_cap_hkd=4000,audit=lambda *args:None)
            try:
                with self.assertRaisesRegex(SafetyError,'Caller does not hold'):reserve_buy(root,broker,paths,request,**args)
                with admission_lock(root):
                    with self.assertRaisesRegex(SafetyError,'positive HK news'):reserve_buy(root,broker,paths,request,**args)
                    self.assertEqual(reservations.committed_and_held(),0)
                    controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False,require_news_opportunity=False)))
                    self.assertTrue(reserve_buy(root,broker,paths,request,**args))
                    self.assertGreater(reservations.committed_and_held(),125)
                    self.assertFalse((base/'hk_risk_snapshot.json').exists())
                self.assertEqual(broker.calls,0)
                for path in paths.values():
                    db=Store(path)
                    try:self.assertEqual(db.load()['cash'],500);self.assertFalse(db.load()['orders'])
                    finally:db.close()
            finally:reservations.close()
