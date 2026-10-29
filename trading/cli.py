import argparse
import json
import time
import sys
from pathlib import Path
from datetime import datetime,timezone
from dataclasses import asdict
from .policy import Config,SafetyError
from .store import Store
from .engine import Engine,TERMINAL
from .moomoo import Moomoo
from .scanner import scan
from .report import weekly_report
from .news import NewsGuard,NewsWorker,refresh_live,DEFAULT_PATH


def read_config(path):
    return Config(**json.loads(Path(path).read_text())).validate() if path else Config()


def doctor(b):
    result={}
    checks={
        'login':lambda:b.call('global',b.q.get_global_state),
        'quotes':lambda:b.snapshots(['US.SPY']),
        'history':lambda:b.history('US.SPY',b.completed_session(datetime.now(timezone.utc))),
        'accounts':b.accounts,
        'quota':lambda:b.call('quota',b.q.get_history_kl_quota,True),
    }
    for name,fn in checks.items():
        try:
            data=fn()
            if name=='accounts':
                # Explicit account IDs printed only here, for the owner to bind; never log them remotely.
                result[name]=dict(ok=True,accounts=[{k:r.get(k) for k in ('acc_id','trd_env','security_firm','acc_status','sim_acc_type')} for r in data])
            elif name=='login':result[name]=dict(ok=bool(data.get('qot_logined') and data.get('trd_logined')),**{k:data.get(k) for k in ('qot_logined','trd_logined','server_ver')})
            else:result[name]=dict(ok=True)
        except Exception as exc:result[name]=dict(ok=False,error=str(exc).split('https://')[0])
    return result


def paper_evidence(path,kind):
    db=Store(path)
    try:s=db.load()
    finally:db.close()
    if s['env']!='SIMULATE':raise SafetyError('Evidence must come from a paper account')
    if s.get('purpose')=='transport_probe':raise SafetyError('Transport probe is not strategy acceptance evidence')
    if s.get('purpose')=='strategy_simulation' and not s.get('strategy_acceptance'):
        raise SafetyError('Strategy acceptance has not been recorded')
    orders=[o for o in s['orders'].values() if o['kind']==kind]
    if any(o['status'] not in TERMINAL for o in orders):raise SafetyError('Paper orders unresolved')
    if not any(o['side']=='BUY' and o['booked_qty']>0 for o in orders) or not any(o['side']=='SELL' and o['booked_qty']>0 for o in orders):
        raise SafetyError('Need an observed paper buy/sell lifecycle for this product')
    if any(p['qty'] for p in s['positions'].values() if p['kind']==kind):raise SafetyError('Paper lifecycle must be flat')
    if s['paused'] or not s['marks']:raise SafetyError('Paper reconciliation/valuation not clean')
    return True


def main(argv=None):
    p=argparse.ArgumentParser(description='moomoo 自动交易；实盘默认关闭')
    p.add_argument('--db',default='data/trading/paper.sqlite')
    p.add_argument('--config')
    p.add_argument('--port',type=int,default=11111)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor');sub.add_parser('status')
    bind=sub.add_parser('bind');bind.add_argument('--account',required=True);bind.add_argument('--env',choices=['REAL','SIMULATE'],required=True);bind.add_argument('--fx',type=float,required=True)
    for cmd in ('scan','options-scan','run'):
        a=sub.add_parser(cmd);a.add_argument('--market-dir',default='data/trading/market')
        if cmd=='run':
            a.add_argument('--once',action='store_true');a.add_argument('--research-options',action='store_true')
            a.add_argument('--news-path',default=DEFAULT_PATH);a.add_argument('--sources',default='trading/external_news.example.json')
    for cmd in ('pause','stop','resume'):sub.add_parser(cmd)
    enable=sub.add_parser('enable');enable.add_argument('--product',choices=['stocks','options'],required=True);enable.add_argument('--paper-db',required=True)
    enable.add_argument('--option-evidence',help='真实历史期权研究报告 JSON；未提供则不启用实盘期权')
    report=sub.add_parser('report');report.add_argument('--output',default='data/trading/reports')
    news=sub.add_parser('news');news.add_argument('--codes',nargs='+',required=True);news.add_argument('--output',default=DEFAULT_PATH);news.add_argument('--sources',default='trading/external_news.example.json');news.add_argument('--watch',action='store_true')
    args=p.parse_args(argv)
    if args.command=='news':
        try:
            while True:
                r=refresh_live(args.codes,args.output,args.port,args.sources)
                print(json.dumps(r['health'],ensure_ascii=False,indent=2),flush=True)
                result=0 if all(r['health'][k]['ok'] for k in r.get('required_sources',list(r['health']))+args.codes) else 1
                if not args.watch:return result
                time.sleep(60)
        except KeyboardInterrupt:return 0
    config=read_config(args.config);db=Store(args.db);broker=None;news_worker=None
    try:
        if args.command in ('status','report','pause','stop','resume'):
            with db.lock():
                s=db.load()
                if args.command=='status':
                    print(json.dumps({k:s.get(k) for k in ('env','cash','paused','stopped','enabled_stocks','enabled_options','alerts','news_status')},ensure_ascii=False,indent=2));return 0
                if args.command=='report':weekly_report(s,args.output);print(str(Path(args.output).resolve()));return 0
                if args.command=='pause':s['paused']=True;s['alerts'].append('User paused entries')
                if args.command=='stop':s['stopped']=True;s['paused']=True
                if args.command=='resume':
                    # Manual resume does not erase loss, unresolved orders, or enable live switches.
                    s['stopped']=False;s['paused']=False;s['alerts']=[]
                db.save(s,args.command)
            if args.command in ('pause','stop'):
                # Persist local control even if OpenD is unavailable; try cancellation now.
                try:
                    broker=Moomoo(args.port);engine=Engine(broker,db,config)
                    with db.lock():s=engine.load();engine.manage_orders(s,datetime.now(timezone.utc))
                except Exception as exc:print('本地控制已保存；撤单尚未确认：'+str(exc),file=sys.stderr)
            print('状态已保存；恢复运行时将重新核对风险和账户。');return 0
        broker=Moomoo(args.port)
        if args.command=='doctor':
            results=doctor(broker);print(json.dumps(results,ensure_ascii=False,indent=2,default=str))
            return 0 if all(r['ok'] for r in results.values()) else 1
        engine=Engine(broker,db,config)
        if args.command=='bind':
            with db.lock():engine.bind(args.account,args.env,args.fx,datetime.now(timezone.utc))
            print('账户已绑定；实盘开关仍关闭。');return 0
        if args.command=='enable':
            import unittest
            suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1]/'tests'))
            result=unittest.TextTestRunner(verbosity=1).run(suite)
            if result.testsRun<20 or not result.wasSuccessful():raise SafetyError('Protection tests failed/incomplete')
            with db.lock():
                s=engine.load()
                if s['env']!='REAL':raise SafetyError('Enable command only applies to explicit REAL binding')
                product='STOCK' if args.product=='stocks' else 'OPTION'
                paper_evidence(args.paper_db,product)
                checks=doctor(broker)
                if not all(c['ok'] for c in checks.values()):raise SafetyError('API readiness checks failed')
                engine.reconcile(s,datetime.now(timezone.utc));engine.mark(s,datetime.now(timezone.utc))
                if s['paused'] or engine.blocked_orders(s):raise SafetyError('Unresolved risk/order state')
                if args.product=='options':
                    if not config.live_options:raise SafetyError('Set live_options in config explicitly first')
                    from .option_research import validate_evidence
                    validate_evidence(args.option_evidence)
                    s['option_validation']=True;s['enabled_options']=True;s['options_enabled_at']=datetime.now(timezone.utc).isoformat()
                else:
                    if not config.live_stocks:raise SafetyError('Set live_stocks in config explicitly first')
                    s['enabled_stocks']=True;s['stocks_enabled_at']=datetime.now(timezone.utc).isoformat()
                db.save(s,'enabled',{'product':args.product})
            print('实盘产品开关已启用；启动 run 后才按规则运行。');return 0
        if args.command=='options-scan':
            market=Path(args.market_dir);snapshot=json.loads((market/'scan.json').read_text())
            if not snapshot.get('complete'):raise SafetyError('Complete stock scan required before option research')
            with db.lock():s=engine.load()
            contracts=[];errors={};now=datetime.now(timezone.utc)
            from .moomoo import NY
            for code,signal in sorted(snapshot['signals'].items(),key=lambda x:(-abs(x[1]['strength']),x[0])):
                if not signal['liquid']:continue
                try:
                    chain=broker.option_chain(code,now.astimezone(NY).date())
                    contracts.extend(broker.option_data(chain,now))
                except Exception as exc:
                    errors[code]=str(exc)
                    if len(errors)>=3:break
            observed=datetime.now(timezone.utc)
            result=dict(signal_date=snapshot['date'],observed_at=observed.isoformat(),complete=not errors,contracts=contracts,errors=errors)
            (market/'option_observations.json').write_text(json.dumps(result,default=str))
            print('期权真实行情研究完成；不生成股票收益代理的期权收益。',len(contracts),'个合约',errors)
            return 0 if not errors else 1
        if args.command=='scan':
            with db.lock():
                s=engine.load();held=[p.get('underlying') or c for c,p in s['positions'].items() if p['qty']]
            from .rolling_scan import DailyBroker
            daily=DailyBroker(args.port)
            try:r=scan(daily,args.market_dir,datetime.now(timezone.utc),held)
            finally:daily.close()
            print(json.dumps({k:r[k] for k in ('date','complete','eligible_count','history_count','errors')},ensure_ascii=False))
            return 0 if r['complete'] else 1
        from .automation import ScanSchedule,CalendarFeed,session_schedule
        from .desktop_server import Controller
        scan_controller=Controller();scan_controller.scan_args=['--market-dir',args.market_dir,'--port',str(args.port),'--db',args.db]
        scan_schedule=ScanSchedule(scan_controller,Path(args.market_dir).parent/'scan_schedule.json',args.market_dir)
        scan_calendar=CalendarFeed()
        engine.news=NewsGuard(args.news_path)
        news_worker=NewsWorker(args.news_path,args.port,args.sources)
        while True:
            now=datetime.now(timezone.utc);market=Path(args.market_dir);file=market/'scan.json'
            snapshot=json.loads(file.read_text()) if file.exists() else {}
            try:
                state=db.load()
                held=[v.get('underlying') or c for c,v in state['positions'].items() if v['qty']]
                pending=[o.get('underlying') or o['code'] for o in state['orders'].values() if o['status'] not in TERMINAL]
                watched=list(dict.fromkeys(held+pending+[c['code'] for c in snapshot.get('candidates',[])[:10]]))
                if not state['stopped']:news_worker.poll(watched)
                with db.lock():
                    # Reload controls each cycle so pause/stop from another process is respected.
                    engine.tick(snapshot,now);engine.drain(snapshot,datetime.now(timezone.utc))
                    s=db.load()
                    if args.research_options and not s['paused'] and not s['stopped'] and broker.execution_session(now):
                        option_file=market/'option_observations.json'
                        if option_file.exists():
                            observations=json.loads(option_file.read_text())
                            if observations.get('complete') and observations.get('signal_date')==snapshot.get('date'):
                                from .policy import option_candidates
                                # Expensive chain collection is a separate command. Refresh a ranked shortlist's quotes here.
                                raw=observations['contracts']
                                shortlist=option_candidates(raw,snapshot['signals'],now.date(),
                                    datetime.fromisoformat(observations['observed_at']),config.option_cap_nzd*s['fx'],config.option_fee_reserve_usd)
                                contracts=[]
                                if shortlist:
                                    c=shortlist[0]
                                    latest=broker.snapshots([c['code']])[0]
                                    c.update(delta=latest.get('option_delta'),open_interest=latest.get('option_open_interest'),
                                        volume=latest.get('volume'),snapshot_date=latest['update_time'][:10],book=broker.book(c['code']))
                                    contracts=[c]
                                engine.option_entry(snapshot,contracts,datetime.now(timezone.utc))
                target,next_run=session_schedule(scan_calendar.read(now),now)
                scan_schedule.tick(now,target,next_run)
                weekly_report(db.load(),'data/trading/reports')
            except Exception as exc:
                print('运行暂停新买入：'+str(exc),file=sys.stderr,flush=True)
                with db.lock():
                    s=db.load();engine.alert(s,'Runtime error: '+str(exc))
                # SDK normally reconnects internally. Next iteration always reconciles before orders.
            if args.once:break
            time.sleep(10)
        return 0
    except (SafetyError,OSError,ValueError,KeyError) as exc:
        print('未完成：'+str(exc),file=sys.stderr);return 1
    finally:
        if news_worker:news_worker.close()
        if broker:broker.close()
        db.close()

if __name__=='__main__':raise SystemExit(main())
