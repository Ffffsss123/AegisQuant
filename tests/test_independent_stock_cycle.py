import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from trading.engine import Engine
from trading.swing_engine import SwingEngine
from trading.swing_model import VERSION
from trading.policy import Config
from trading.store import Store
from trading.hk_reservations import Reservations
from trading.simulation_loss_latch import initialize
from trading.independent_stock_cycle import run_once
from tests.test_trading import Fake

class StockCycle(unittest.TestCase):
    def test_native_buy_then_paused_trend_exit_without_hk(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data/trading';(base/'market').mkdir(parents=True);(base/'news').mkdir()
            now=datetime.now(timezone.utc);day=now.date().isoformat();previous=(now-timedelta(days=1)).date().isoformat()
            broker=Fake();broker.now=lambda:datetime.now(timezone.utc)
            broker.execution_session=lambda now,strict=True:(day,previous)
            broker.completed_session=lambda now:previous
            broker.calendar=lambda now:[dict(time=d,trade_date_type='WHOLE') for d in (previous,day)]
            broker.session_times=lambda day,kind:(now-timedelta(hours=1),now+timedelta(hours=1))
            broker.book=lambda code:dict(bid=10,ask=10.01,bid_time=broker.now().isoformat(),ask_time=broker.now().isoformat())
            paths={name:base/(name+'.sqlite') for name in ('US_STOCK','US_OPTION')}
            for name,path in paths.items():
                db=Store(path);cls=SwingEngine if name=='US_STOCK' else Engine
                extra=dict(capital_mode='independent_sleeves_v1',sleeve=name,portfolio_id='p',shared_capital=False)
                if name=='US_STOCK':extra.update(purpose='strategy_simulation',strategy_version=VERSION,entry_timing='regular_session',entry_after_session=previous)
                cls(broker,db,Config(budget_usd=500,loss_limit_usd=100,option_cap_usd=500)).bind('chosen','SIMULATE',.6,now,extra=extra);db.close()
            Reservations(base/'hk_reservations.sqlite','p').close();initialize(base/'simulation_loss_latch.sqlite','p',inherited_stop=False)
            hk=dict(as_of=now.isoformat(),account='hk',portfolio_id='p',env='SIMULATE',currency='USD',reconciled=True,pending_or_unknown=False,entry_block=False,budget_usd=500,loss_limit_usd=100,committed_debit_usd=0,held_usd=0,exit_fee_reserve_usd=0,occupied_usd=0,realized_net_usd=0,unrealized_net_usd=0)
            (base/'hk_risk_snapshot.json').write_text(json.dumps(hk))
            controls=base/'simulation_controls.json';controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
            (base/'market/scan.json').write_text(json.dumps(dict(date=previous,generated_at='stamp',complete=True,signals={'US.A':{}})))
            scan=dict(version=VERSION,date=previous,source_generated_at='stamp',complete=True,signals={'US.A':dict(sell=False,buy=True,liquid=True,stop_distance=1,target_distance=2)},candidates=[dict(code='US.A')])
            (base/'market/short_scan.json').write_text(json.dumps(scan))
            (base/'news/latest.json').write_text(json.dumps(dict(version=3,collected_at=now.isoformat(),required_sources=['source'],health={k:dict(ok=True,checked_at=now.isoformat()) for k in ('source','US.A')},events=[])))
            args=dict(account='chosen',hk_account='hk',portfolio='p',archive=dict(env='SIMULATE',account='chosen',sealed=True,orders=[],history_start=day),refresh_hk=lambda:dict(status='reconciled'))
            result=run_once(root,broker,paths,**args)
            self.assertTrue(result.get('acknowledged'),result);self.assertEqual(broker.calls,1)
            qty=broker.remote[-1]['qty'];broker.fill(qty,10.01)
            controls.write_text(json.dumps(dict(paused=True,stopped=False,reset_pending=False)))
            scan['signals']['US.A']['sell']=True;(base/'market/short_scan.json').write_text(json.dumps(scan))
            (base/'hk_risk_snapshot.json').unlink()
            args['refresh_hk']=lambda:(_ for _ in ()).throw(RuntimeError('HK unavailable'))
            result=run_once(root,broker,paths,**args)
            self.assertEqual(result['status'],'exit_serviced');self.assertEqual(broker.calls,2)
            self.assertEqual(broker.remote[-1]['side'],'SELL');self.assertEqual(broker.remote[-1]['qty'],qty)
            run_once(root,broker,paths,**args);self.assertEqual(broker.calls,2)
