"""Fixed local desktop actions. No REAL order creation or enable operation."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from .moomoo import Moomoo
from .store import Store
from .policy import SafetyError,Config
from .capital import config_for_state

ROOT=Path(__file__).resolve().parents[1]

def resume_empty_probe(db,b):
    from .engine import Engine
    with db.lock():
        s=db.load()
        if s.get('purpose')!='transport_probe' or s.get('env')!='SIMULATE':raise SafetyError('Not a simulation probe')
        controls_path=ROOT/'data/trading/simulation_controls.json'
        controls=json.loads(controls_path.read_text()) if controls_path.exists() else {}
        if controls.get('paused') or controls.get('stopped'):raise SafetyError('用户控制仍暂停模拟任务')
        if s.get('stopped'):raise SafetyError('模拟任务已手动停止；不能自动恢复')
        if not s.get('paused'):return s['account']
        allowed=('Order book timestamp unavailable','Waiting for timestamped order book','No valid quote within',
                 'Stale/future order book','Disconnect','网络中断','OpenD 未连接','Quote connection interrupted','Read-only query timeout:')
        def recoverable(a):
            return any(k in a for k in allowed) or (a in ('SafetyError: NN_ProtoRet_TimeOut','SafetyError: Spread too wide') and s.get('probe_phase')=='cancel_test' and not s.get('orders'))
        if not s.get('alerts') or any(not recoverable(a) for a in s['alerts']):
            raise SafetyError('暂停原因不是可恢复的报价或连接故障')
        if s['orders'] or any(p['qty'] for p in s['positions'].values()):raise SafetyError('模拟有订单或持仓，不能自动恢复')
        e=Engine(b,db,Config());e.load();e.reconcile(s,datetime.now(timezone.utc))
        if any(not recoverable(a) for a in s['alerts']):raise SafetyError('账户对账不一致')
        if any(b.positions().values()) or b.orders(s['bound_at'][:10]):raise SafetyError('模拟账户有持仓或订单历史，不能自动恢复')
        s['paused']=False;s['alerts']=[];s.pop('quote_wait',None);s.pop('query_retry',None);db.save(s,'desktop_restart_empty_probe')
        return s['account']

def account_snapshot():
    b=None;out=ROOT/'data/desktop/account.json'
    result={'updated_at':datetime.now(timezone.utc).isoformat(),'accounts':[]}
    try:
        b=Moomoo()
        for a in b.accounts():
            if a['trd_env']!='REAL' or a['acc_status']!='ACTIVE' or a['security_firm']!='FUTUAU':continue
            kw=dict(trd_env=b.m.TrdEnv.REAL,acc_id=int(a['acc_id']))
            p=b.call('positions',b.t.position_list_query,refresh_cache=True,**kw).to_dict('records')
            cash=b.call('cash',b.t.accinfo_query,currency=b.m.Currency.USD,refresh_cache=True,**kw).to_dict('records')
            result['accounts'].append(dict(suffix=str(a['acc_id'])[-4:],env='REAL',
                positions=[{k:r.get(k) for k in ('code','stock_name','qty','market_val','pl_val','currency','cost_price','cost_price_valid','nominal_price')} for r in p if float(r.get('qty',0))],
                cash=[{k:r.get(k) for k in ('hk_cash','us_cash','us_avl_withdrawal_cash')} for r in cash]))
    except Exception as exc:result['error']=str(exc).split('https://')[0]
    finally:
        if b:b.close()
        out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,default=str));tmp.replace(out)

def probe():
    from .paper_probe import PaperOnly,main
    from .engine import Engine
    db=Store(ROOT/'data/trading/probe.sqlite');b=PaperOnly()
    try:
        account=resume_empty_probe(db,b)
    finally:db.close();b.close()
    return main(['--account',account,'--wait-open'])

def pause(stop=False):
    from .paper_probe import PaperOnly
    from .engine import Engine
    for name in ('paper.sqlite','probe.sqlite','option_transport.sqlite'):
        path=ROOT/'data/trading'/name
        if not path.exists():continue
        db=Store(path);broker=None
        try:
            if not db.exists():continue
            with db.lock():
                s=db.load()
                if s['env']!='SIMULATE':raise SafetyError('Desktop controls only SIMULATE')
                s['paused']=True
                if stop:s['stopped']=True
                db.save(s,'desktop_worker_stop' if stop else 'desktop_worker_pause')
            from .option_transport import active
            if active(ROOT) and name!='option_transport.sqlite':continue
            broker=PaperOnly();engine=Engine(broker,db,config_for_state(db.load()))
            with db.lock():
                s=engine.load();engine.manage_orders(s,datetime.now(timezone.utc))
        finally:
            if broker:broker.close()
            db.close()

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='account':account_snapshot()
    elif mode=='probe':raise SystemExit(probe())
    elif mode in ('pause','stop'):pause(stop=mode=='stop')
    else:raise SafetyError('Unsupported desktop action')
