"""HK completed-session scans with persistent retries, independent of orders."""
from datetime import datetime, timedelta
from .desktop_server import read_json
from .hk_policy import HK
from .hk_scan import completed_session
from .rolling_scan import atomic
from .policy import SafetyError

class HKScanSchedule:
    def __init__(self, controller, root):
        self.c,self.root=controller,root
        self.path=root/'data/trading/hk_scan_schedule.json'
    def tick(self,now):
        market=self.root/'data/trading/hk_market';today=now.astimezone(HK).date().isoformat()
        calendar=read_json(market/'scan_calendar.json')
        covered=calendar.get('range_end','')>=today
        try:
            target=completed_session(calendar['rows'],now) if covered else 'refresh_calendar_'+today
        except (ValueError,KeyError,TypeError,RuntimeError,SafetyError) as exc:
            state=dict(status='needs_attention',reason=str(exc),updated_at=now.isoformat(),inflight=False)
            atomic(self.path,state);return state
        s=read_json(self.path)
        if s.get('target_date')!=target:s=dict(target_date=target,retries=0,inflight=False,status='pending')
        s['updated_at']=now.isoformat()
        result=read_json(market/'scan.json')
        catalog=read_json(market/'catalog.json')
        if covered and result.get('date')==target and result.get('complete') is True and not result.get('errors'):
            s.update(status='complete',inflight=False,reason=None,retry_at=None)
        elif self.c.running('hk_scan'):s.update(status='running',inflight=True)
        elif s.get('inflight'):
            delays=(300,900,1800);used=s['retries'];s.update(inflight=False,reason='HK scan exited without complete target data')
            if used>=len(delays):s.update(status='needs_attention',retry_at=None)
            else:s.update(status='retry_wait',retries=used+1,retry_at=(now+timedelta(seconds=delays[used])).isoformat())
        elif catalog.get('source_date')!=today:
            s['reason']='Waiting for current HK directory'
        elif s.get('status')!='needs_attention' and (not s.get('retry_at') or now>=datetime.fromisoformat(s['retry_at'])):
            s.update(status='running',inflight=True);atomic(self.path,s)
            try:self.c.start('hk_scan')
            except Exception as exc:s['reason']=str(exc)
        atomic(self.path,s);return s
