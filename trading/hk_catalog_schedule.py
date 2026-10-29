"""Independent daily HK identity refresh with bounded persistent retries."""
from datetime import datetime,timedelta
from .hk_policy import HK
from .desktop_server import read_json
from .rolling_scan import atomic

class CatalogSchedule:
    def __init__(self,controller,root):
        self.controller=controller;self.root=root
        self.path=root/'data/trading/hk_catalog_schedule.json'
    def tick(self,now):
        day=now.astimezone(HK).date().isoformat();s=read_json(self.path)
        if s.get('target_date')!=day:s=dict(target_date=day,retries=0,status='pending',inflight=False)
        s['updated_at']=now.isoformat()
        catalog=read_json(self.root/'data/trading/hk_market/catalog.json')
        if catalog.get('source_date')==day:
            s.update(status='complete',inflight=False,reason=None,retry_at=None)
        elif self.controller.running('hk_catalog'):s.update(status='running',inflight=True)
        elif s.get('inflight'):
            delays=(300,900,1800);used=s['retries'];s.update(inflight=False,reason='HK catalog process exited without today’s directory')
            if used>=len(delays):s.update(status='needs_attention',retry_at=None)
            else:s.update(status='retry_wait',retries=used+1,retry_at=(now+timedelta(seconds=delays[used])).isoformat())
        elif s.get('status')!='needs_attention' and (not s.get('retry_at') or now>=datetime.fromisoformat(s['retry_at'])):
            s.update(status='running',inflight=True);atomic(self.path,s)
            try:self.controller.start('hk_catalog')
            except Exception as exc:s['reason']=str(exc)
        atomic(self.path,s);return s
