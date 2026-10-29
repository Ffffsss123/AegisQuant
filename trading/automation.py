"""Persistent local scheduling; quote collection and simulation run in separate processes."""
import fcntl,json,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .moomoo import NY
from .rolling_scan import DailyBroker,atomic
from .desktop_server import Controller,ROOT,DATA,read_json,ledger
from .policy import SafetyError

RETRIES=(300,900,1800)

def session_schedule(rows,now):
    from .moomoo import Moomoo
    due=[]
    for row in rows:
        _,close=Moomoo.session_times(None,row['time'],row['trade_date_type'])
        due.append((close+timedelta(minutes=5),row['time']))
    past=[(at,day) for at,day in due if at<=now]
    future=[at for at,_ in due if at>now]
    if not past or not future:raise SafetyError('Exchange calendar does not cover schedule')
    return max(past)[1],min(future).isoformat()

class CalendarFeed:
    def __init__(self):self.day=None;self.rows=None
    def read(self,now):
        day=now.astimezone(NY).date()
        if self.day!=day:
            b=DailyBroker()
            try:
                self.rows=b.call('calendar',b.q.request_trading_days,market=b.m.TradeDateMarket.US,
                    start=(day-timedelta(days=160)).isoformat(),end=(day+timedelta(days=14)).isoformat())
                session_schedule(self.rows,now);self.day=day
            finally:b.close()
        return self.rows

class ScanSchedule:
    def __init__(self,controller,path,market):self.c=controller;self.path=Path(path);self.market=Path(market)
    def tick(self,now,target,next_run):
        s=read_json(self.path)
        if s.get('target_date')!=target:
            s=dict(target_date=target,retries=0,status='pending',inflight=False)
        s.update(updated_at=now.isoformat(),next_run_at=next_run)
        result=read_json(self.market/'scan.json')
        if result.get('date')==target and result.get('complete'):
            s.update(status='complete',inflight=False,reason=None,retry_at=None)
        elif self.c.running('scan'):
            s.update(status='running',inflight=True)
        elif s.get('inflight'):
            progress=read_json(self.market/'scan_progress.json')
            reason=progress.get('errors') or 'Scan exited before publishing a complete result'
            s.update(reason=str(reason),inflight=False)
            used=s.get('retries',0)
            if used>=len(RETRIES):s.update(status='needs_attention',retry_at=None)
            else:s.update(status='retry_wait',retry_at=(now+timedelta(seconds=RETRIES[used])).isoformat(),retries=used+1)
        elif s.get('status')!='needs_attention' and (not s.get('retry_at') or now>=datetime.fromisoformat(s['retry_at'])):
            # Persist claim before process creation. A crash costs one bounded retry.
            s.update(status='running',inflight=True,retry_at=None);atomic(self.path,s)
            try:self.c.start('scan')
            except Exception as exc:s['reason']=str(exc)
        atomic(self.path,s);return s

class Supervisor:
    def __init__(self,controller,root=ROOT):
        self.c=controller;self.root=Path(root);self.path=self.root/'data/trading/automation.json'
        self.schedule=ScanSchedule(controller,self.root/'data/trading/scan_schedule.json',self.root/'data/trading/market')
        self.calendar=CalendarFeed()
    def tick(self,now):
        state=read_json(self.path);state.update(updated_at=now.isoformat(),enabled=True)
        from .hk_catalog_schedule import CatalogSchedule
        if (self.root/'data/trading/hk_submissions.sqlite').exists():
            state['hk_catalog']=CatalogSchedule(self.c,self.root).tick(now)
            from .hk_scan_schedule import HKScanSchedule
            state['hk_scan']=HKScanSchedule(self.c,self.root).tick(now)
        day=now.astimezone(NY).date().isoformat()
        if state.get('calendar_retry_day')!=day:
            state.update(calendar_retry_day=day,calendar_failures=0,calendar_retry_at=None)
        retry=state.get('calendar_retry_at')
        if state.get('calendar_failures',0)<=len(RETRIES) and (not retry or now>=datetime.fromisoformat(retry)):
            try:
                target,next_run=session_schedule(self.calendar.read(now),now)
                self.schedule.tick(now,target,next_run);state.pop('scan_error',None)
                state.update(calendar_failures=0,calendar_retry_at=None)
            except Exception as exc:
                # Calendar failures cannot authorize a guessed trading day.
                used=state.get('calendar_failures',0);state['calendar_failures']=used+1
                state['calendar_retry_at']=(now+timedelta(seconds=RETRIES[used])).isoformat() if used<len(RETRIES) else None
                state['scan_error']=str(exc)+(' · 等待自动重试' if used<len(RETRIES) else ' · 重试耗尽，需要处理')
        probe=ledger(self.root/'data/trading/probe.sqlite')
        paper=ledger(self.root/'data/trading/paper.sqlite')
        if paper and (paper.get('env')!='SIMULATE' or paper.get('purpose')!='strategy_simulation'):
            state['handoff']='blocked';state['reason']='Unexpected strategy ledger; no automatic reset'
        elif self.c.running('strategy'):
            state['handoff']='strategy_running';state.pop('reason',None)
        elif not paper and self.c.running('probe'):
            state['handoff']='waiting_probe_exit'
        elif not paper and (probe.get('paused') or probe.get('stopped')):
            state['handoff']='blocked';state['reason']='接口验收已暂停：'+'; '.join(probe.get('alerts',[]))
        elif not paper and probe.get('probe_phase')!='complete':
            state['handoff']='waiting_interface_acceptance'
        else:
            report=read_json(self.root/'data/trading/strategy.report.json')
            if not paper and report.get('status')=='needs_attention':
                state['handoff']='blocked';state['reason']=report.get('reason','Strategy requires inspection')
            elif not state.get('strategy_retry_at') or now>=datetime.fromisoformat(state['strategy_retry_at']):
                state['strategy_retry_at']=(now+timedelta(minutes=5)).isoformat()
                atomic(self.path,state)
                try:self.c.start('strategy');state['handoff']='checking_account_and_handoff';state.pop('reason',None)
                except Exception as exc:state['handoff']='blocked';state['reason']=str(exc)
        atomic(self.path,state);return state

def main():
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'automation.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        supervisor=Supervisor(Controller())
        while True:
            try:supervisor.tick(datetime.now(timezone.utc))
            except Exception as exc:print('Automation check failed: '+str(exc),flush=True)
            time.sleep(30)

if __name__=='__main__':main()
