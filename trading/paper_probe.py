"""Broker transport acceptance in an isolated SIMULATE account ledger.
Not a strategy signal, profitability test, or evidence permitting live activation.
"""
import argparse,json,time,fcntl
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .moomoo import Moomoo
from .engine import Engine,TERMINAL
from .store import Store
from .policy import Config,SafetyError,quote_price
from .quote_feed import QuoteUnavailable
from .read_retry import ReadQueryTimeout

class PaperOnly(Moomoo):
    def bind(self,account,env):
        if env!='SIMULATE':raise SafetyError('Probe only supports SIMULATE')
        super().bind(account,env)
    def kw(self):
        if self.env!='SIMULATE':raise SafetyError('Probe cannot access REAL operations')
        return super().kw()

class ProbeStep:
    """One non-sleeping iteration; order servicing always precedes quote waiting."""
    def __init__(self,engine,code):self.e=engine;self.code=code;self.wait_started=None
    def submit(self,s,side,qty,price,phase,now):
        old_phase=s['probe_phase'];old_orders=set(s['orders'])
        s['probe_phase']=phase;self.e.store.save(s,'probe_phase_claim')
        try:self.e.submit(s,self.code,side,qty,price,now)
        except SafetyError as exc:
            if not isinstance(exc,(QuoteUnavailable,ReadQueryTimeout)) and str(exc) not in ('Stale/future quote','Spread too wide'):raise
            if set(s['orders'])!=old_orders:raise SafetyError('Quote failure after durable order intent; inspect before retry')
            s['probe_phase']=old_phase;self.e.store.save(s,'probe_quote_deferred')
            if isinstance(exc,(QuoteUnavailable,ReadQueryTimeout)):raise
            raise QuoteUnavailable(str(exc),'waiting_spread' if str(exc)=='Spread too wide' else 'quote_expired') from exc
    def advance(self,s,now):
        e=self.e;b=e.b;phase=s['probe_phase']
        orders=list(s['orders'].values());owned=s['positions'].get(self.code,{}).get('qty',0)
        if phase in ('cancel_test','buy','sell'):
            side='SELL' if phase=='sell' else 'BUY'
            try:price=quote_price(b.book(self.code),side,b.now())
            except SafetyError as exc:
                if str(exc) in ('Stale/future quote','Spread too wide'):raise QuoteUnavailable(str(exc),'waiting_spread' if str(exc)=='Spread too wide' else 'quote_expired') from exc
                raise
            if phase=='cancel_test':self.submit(s,side,1,round(price*.5,2),'cancel_wait',now)
            elif phase=='buy':self.submit(s,side,1,price,'buy_wait',now)
            else:self.submit(s,side,owned,price,'sell_wait',now)
        elif phase=='cancel_wait':
            if owned:raise SafetyError('Cancellation probe unexpectedly filled; inspect simulation position')
            if not orders or not orders[-1].get('cancel_requested') or orders[-1]['status']!='CANCELLED_ALL':
                raise SafetyError('Cancellation probe did not produce verified cancellation')
            s['probe_phase']='buy';e.store.save(s,'probe_phase')
        elif phase=='buy_wait':
            if owned<=0:raise SafetyError('Simulation buy unfilled; no automatic retry')
            s['probe_phase']='sell';e.store.save(s,'probe_phase')
        elif phase=='sell_wait':
            if owned:raise SafetyError('Simulation exit unfilled; inspect position')
            e.mark(s,now);s['probe_phase']='complete';e.store.save(s,'probe_complete')
            return 'transport_passed'
        else:raise SafetyError('Unknown simulation phase')
        return 'running'
    def tick(self,s,now):
        controls=self.e.controls() if hasattr(self.e,'controls') else {}
        if controls.get('paused') or controls.get('stopped'):
            s['paused']=True
            if controls.get('stopped'):s['stopped']=True
        retry=s.get('query_retry',{})
        if retry.get('exhausted'):return 'needs_attention'
        if retry.get('next_at') and now<datetime.fromisoformat(retry['next_at']):return 'query_retry'
        try:
            if retry.get('failures'):self.e.reconcile(s,now)
            status=self.advance_tick(s,now)
        except ReadQueryTimeout as exc:
            count=retry.get('failures',0)+1
            delays=(5,15,30)
            s['query_retry']=dict(interface=exc.interface,failures=count,exhausted=count>len(delays),
                next_at=(now+timedelta(seconds=delays[count-1])).isoformat() if count<=len(delays) else None)
            self.e.store.save(s,'read_query_timeout',s['query_retry'])
            if count>len(delays):
                self.e.alert(s,'Read-only query retries exhausted: '+exc.interface);return 'needs_attention'
            return 'query_retry'
        s.pop('query_retry',None);self.e.store.save(s,'read_query_recovered')
        return status
    def advance_tick(self,s,now):
        e=self.e
        e.manage_orders(s,now)  # Includes remote reconciliation, even while waiting.
        if s['paused'] or s['stopped']:return 'needs_attention'
        if s['probe_phase']=='complete':return 'transport_passed'
        if not e.b.execution_session(now,strict=False):return 'waiting_regular_session_0935_ET'
        if e.blocked_orders(s):return 'running'
        saved_wait=s.get('quote_wait') or {}
        if self.wait_started is None and saved_wait.get('started_at'):
            age=max(0,(now-datetime.fromisoformat(saved_wait['started_at'])).total_seconds())
            self.wait_started=time.monotonic()-age
        if self.wait_started is not None and time.monotonic()-self.wait_started>=120:
            raise QuoteUnavailable('No valid quote within 120 seconds','quote_wait_timeout')
        try:status=self.advance(s,now)
        except QuoteUnavailable as exc:
            if exc.status=='connection_interrupted':raise
            if self.wait_started is None:self.wait_started=time.monotonic()
            s['quote_wait']={'status':exc.status,'reason':str(exc),'elapsed_seconds':round(time.monotonic()-self.wait_started,1),
                'started_at':saved_wait.get('started_at') or now.isoformat()}
            e.store.save(s,'probe_waiting_quote',s['quote_wait'])
            return exc.status
        self.wait_started=None;s.pop('quote_wait',None);e.store.save(s,'probe_quote_ready')
        return status


def main(argv=None):
    p=argparse.ArgumentParser(description='仅模拟账户接口验收，不开启实盘')
    p.add_argument('--account',required=True);p.add_argument('--code',default='US.AA')
    p.add_argument('--db',default='data/trading/probe.sqlite');p.add_argument('--wait-open',action='store_true')
    args=p.parse_args(argv)
    if not args.code.startswith('US.'):raise SafetyError('US stock required')
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    runner_lock=open(args.db+'.runner.lock','a')
    try:fcntl.flock(runner_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        runner_lock.close();raise SafetyError('Simulation probe already running')
    try:b=PaperOnly();db=Store(args.db);b.audit=db.event
    except Exception:
        runner_lock.close();raise
    out=Path(args.db).with_suffix('.report.json')
    # Probe tests transport only. News and signal protection are separately tested by the strategy.
    e=Engine(b,db,Config(),news=lambda code,now:dict(code=code,multiplier=1.,reasons=['SIMULATE transport probe only']))
    from .desktop_server import read_json,ROOT
    e.controls=lambda:read_json(ROOT/'data/trading/simulation_controls.json')
    def report(s,status):
        r=dict(status=status,env=s['env'],purpose=s['purpose'],code=args.code,fx_note='Paper-only scale: 0.6 USD/NZD; not a live FX quote',
            phase=s['probe_phase'],orders=[{k:o.get(k) for k in ['side','qty','booked_qty','status']} for o in s['orders'].values()],
            positions=s['positions'],alerts=s['alerts'],updated_at=datetime.now(timezone.utc).isoformat(),
            quote_wait=s.get('quote_wait'),query_retry=s.get('query_retry'),strategy_acceptance=False,live_enabled=False)
        tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2));tmp.replace(out)
        print(status,s['probe_phase'],flush=True)
    try:
        with db.lock():
            if not db.exists():
                s=e.bind(args.account,'SIMULATE',.6,datetime.now(timezone.utc))
                s.update(purpose='transport_probe',probe_code=args.code,probe_phase='cancel_test');db.save(s,'probe_init')
            s=e.load()
            if s.get('purpose')!='transport_probe' or s.get('probe_code')!=args.code or s['account']!=args.account:raise SafetyError('Wrong probe ledger')
        deadline=time.monotonic()+26*3600;step=ProbeStep(e,args.code)
        while True:
            started=time.monotonic();now=datetime.now(timezone.utc)
            with db.lock():
                s=e.load();status=step.tick(s,now);report(s,status)
                if status=='needs_attention':return 1
                if status=='transport_passed':return 0
                if status=='waiting_regular_session_0935_ET' and not args.wait_open:return 2
                if time.monotonic()>deadline:
                    s['stopped']=True;db.save(s,'probe_deadline');e.manage_orders(s,now);report(s,'deadline_expired');return 1
            time.sleep(max(0,5-(time.monotonic()-started)))
    except Exception as exc:
        if db.exists():
            with db.lock():
                s=db.load();s['paused']=True;s['alerts'].append(type(exc).__name__+': '+str(exc));db.save(s,'probe_error')
                try:e.manage_orders(s,datetime.now(timezone.utc))
                except Exception:pass
                status=getattr(exc,'status',None)
                if status is None:status='connection_interrupted' if any(x in str(exc).lower() for x in ('disconnect','未连接','网络中断')) else 'needs_attention'
                report(s,status)
        raise
    finally:db.close();b.close();runner_lock.close()

if __name__=='__main__':raise SystemExit(main())
