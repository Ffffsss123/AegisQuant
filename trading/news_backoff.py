"""Persist source-request backoff without making failed feeds look fresh."""
import hashlib,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.error import HTTPError
from .news import stamp
PATH=Path('data/trading/news/feed_backoff.json')
class FeedBackoff(RuntimeError):
 def __init__(self,retry_at):self.retry_at=retry_at;super().__init__('Public source request deferred')
def fetch_with_backoff(spec,fetch,path=PATH,now=None):
 now=now or datetime.now(timezone.utc);path=Path(path);key=hashlib.sha256(spec['url'].encode()).hexdigest()
 try:state=json.loads(path.read_text())
 except (OSError,ValueError):state={}
 old=state.get(key,{});deadline=stamp(old.get('retry_at'))
 if deadline and now<deadline:raise FeedBackoff(deadline.isoformat())
 try:rows=fetch(spec)
 except HTTPError as exc:
  if exc.code not in (429,503):raise
  count=old.get('failures',0)+1;delay=(300,900,1800)[min(count-1,2)]
  value=exc.headers.get('Retry-After','') if exc.headers else ''
  if str(value).isdigit():delay=max(delay,int(value))
  else:
   requested=stamp(value)
   if requested:delay=max(delay,(requested-now).total_seconds())
  retry=(now+timedelta(seconds=delay)).isoformat()
  state[key]=dict(failures=count,retry_at=retry,source=spec.get('id'),status_code=exc.code)
  _save(path,state);raise FeedBackoff(retry) from None
 if key in state:state.pop(key);_save(path,state)
 return rows
def _save(path,state):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state));tmp.replace(path)
