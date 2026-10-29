"""Public news only: singleton collector, automatic retry, no broker connection."""
import fcntl,json,os,time,re,sqlite3
from datetime import datetime,timezone,timedelta
from .desktop_server import ROOT,DATA,read_json,ledger
from .news import refresh_live,POLL_SECONDS

def watchable(code):
    from .news import security
    return isinstance(code,str) and (security(code)==code or bool(re.fullmatch(r'HK\.[0-9]{5}',code)))

def hk_claimed_codes(path):
    """Retain every claimed HK symbol, including unresolved requests after restart.

    Historical claims are intentionally kept: a stale report must not remove a
    holding from news coverage. Read-only access cannot create a missing ledger.
    Corrupt data raises so the collector does not refresh an incomplete snapshot.
    """
    if not path.exists():return set()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        db.execute('BEGIN')
        binding=db.execute('SELECT account FROM binding WHERE id=1').fetchone()
        if not binding:raise ValueError('Missing HK news ledger binding')
        codes=set()
        for (body,) in db.execute('SELECT request FROM submissions'):
            row=json.loads(body);code=row.get('code')
            if (row.get('account')!=binding[0] or row.get('env')!='SIMULATE'
                or not isinstance(code,str) or not re.fullmatch(r'HK\.[0-9]{5}',code)):
                raise ValueError('Invalid HK news claim identity')
            codes.add(code)
        return codes
    finally:db.close()

def watched_codes():
    codes={'US.SPY','US.AAPL'}
    codes.update(hk_claimed_codes(ROOT/'data/trading/hk_submissions.sqlite'))
    candidates=[]
    for path in ('market/scan.json','hk_market/scan.json'):
        candidates.extend(read_json(ROOT/'data/trading'/path).get('candidates',[]))
    for code in read_json(ROOT/'data/trading/hk_market/watch.json').get('codes',[]):
        if watchable(code):codes.add(code)
    for candidate in candidates:
        code=candidate.get('code')
        if watchable(code):codes.add(code)
    for name in ('paper.sqlite','probe.sqlite','option_transport.sqlite'):
        state=ledger(ROOT/'data/trading'/name)
        from .engine import TERMINAL
        for order in state.get('orders',{}).values():
            code=order.get('underlying') or order.get('code','')
            if watchable(code) and order.get('status') not in TERMINAL:codes.add(code)
        for code,p in state.get('positions',{}).items():
            underlying=p.get('underlying') or code
            if watchable(underlying) and p.get('qty'):codes.add(underlying)
    options=ledger(ROOT/'data/trading/options_observer.sqlite')
    codes.update(c for c in options.get('watch_underlyings',[]) if watchable(c))
    position=options.get('position')
    if position and watchable(position.get('underlying')):codes.add(position['underlying'])
    return sorted(codes)

def collect_once():
    try:
        snapshot=refresh_live(watched_codes())
        from .news_selection import opportunity_pool
        from .rolling_scan import atomic
        atomic(ROOT/'data/trading/news/opportunities.json',opportunity_pool(snapshot,datetime.now(timezone.utc)))
        print(json.dumps({'at':snapshot.get('collected_at'),'health':snapshot.get('health')},ensure_ascii=False),flush=True)
        return True
    except Exception as exc:
        # Keep the old snapshot timestamp: an error must never make stale news fresh.
        print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'error':str(exc)},ensure_ascii=False),flush=True)
        return False

def main():
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'news.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        (DATA/'news_process.json').write_text(json.dumps({'pid':os.getpid()}))
        try:
            while True:
                started=datetime.now(timezone.utc);tick=time.monotonic()
                from .rolling_scan import atomic
                schedule=ROOT/'data/trading/news_schedule.json'
                atomic(schedule,dict(interval_seconds=POLL_SECONDS,status='collecting',started_at=started.isoformat(),next_run_at=(started+timedelta(seconds=POLL_SECONDS)).isoformat()))
                ok=collect_once()
                elapsed=time.monotonic()-tick
                atomic(schedule,dict(interval_seconds=POLL_SECONDS,status='waiting' if ok else 'collection_failed',started_at=started.isoformat(),completed_at=datetime.now(timezone.utc).isoformat(),next_run_at=(started+timedelta(seconds=max(POLL_SECONDS,elapsed))).isoformat()))
                time.sleep(max(0,POLL_SECONDS-elapsed))
        except KeyboardInterrupt:pass
        finally:(DATA/'news_process.json').unlink(missing_ok=True)

if __name__=='__main__':main()
