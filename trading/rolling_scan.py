"""Read-only universe screening with reusable official K_DAY subscriptions."""
import fcntl,json,os,time,math,threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from .moomoo import Moomoo
from .policy import SafetyError,number,trend,candidates
from .scanner import classification,fetch_directories

def atomic(path,data):
    def clean(value):
        if isinstance(value,float) and not math.isfinite(value):return None
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        return value
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    tmp.write_text(json.dumps(clean(data),ensure_ascii=False,allow_nan=False,default=str));tmp.replace(path)

class DailyBroker(Moomoo):
    def __init__(self,port=11111):
        import moomoo as m
        self.m=m;self.t=None;self._last={};self._calendar={};self._daily_lock=threading.Lock()
        self.q=m.OpenQuoteContext(host='127.0.0.1',port=port)
    def subscription_room(self):
        return max(0,int(self.call('subscription',self.q.query_subscription,False)['remain'])-20)
    def subscribe_daily(self,codes):
        return self.call('subscribe',self.q.subscribe,codes,[self.m.SubType.K_DAY],
            subscribe_push=False,extended_time=False,session=self.m.Session.RTH)
    def unsubscribe_daily(self,codes):
        return self.call('unsubscribe',self.q.unsubscribe,codes,[self.m.SubType.K_DAY])
    def daily(self,code,expected):
        # Documented current-candle cache API, not request_history_kline.
        for attempt in range(6):
            with self._daily_lock:
                wait=.25-(time.monotonic()-self._last.get('daily',0))
                if wait>0:time.sleep(wait)
                self._last['daily']=time.monotonic()
            ret,frame=self.q.get_cur_kline(code,120,ktype=self.m.SubType.K_DAY,autype=self.m.AuType.QFQ)
            if ret!=self.m.RET_OK:raise SafetyError(str(frame))
            records=frame.to_dict('records')
            if any(r.get('code')!=code for r in records):raise SafetyError('Unexpected candle identity')
            bars=[dict(date=r['time_key'][:10],**{k:r[k] for k in ('open','high','low','close','volume','turnover')})
                  for r in records if r['time_key'][:10]<=expected]
            if bars and bars[-1]['date']==expected:return bars
            if attempt<5:time.sleep(2)
        return bars

def evaluate_bars(bars,calendar,expected):
    if not bars:return 'excluded_insufficient_history','No completed daily bars'
    dates=[b.get('date') for b in bars]
    if any(not isinstance(d,str) for d in dates) or dates!=sorted(set(dates)):
        return 'excluded_invalid_history','Duplicate or unordered daily bars'
    if dates[-1]!=expected:return 'excluded_stale_history','Last daily bar is not the completed session'
    if len(bars)<60:return 'excluded_insufficient_history','Fewer than 60 completed daily bars'
    if dates[-60:]!=calendar:return 'excluded_history_gaps','Missing exchange sessions in last 60 daily bars'
    try:trend(bars,expected)
    except (SafetyError,KeyError,TypeError,ValueError) as exc:return 'excluded_invalid_history',str(exc)
    return 'ready','Validated completed daily bars'

def snapshot_exclusion(snap,expected):
    if snap.get('_unavailable'):return 'excluded_unavailable',snap['_unavailable']
    if snap.get('suspension') is True:return 'excluded_suspended','Suspended'
    if snap.get('suspension') is not False:return 'excluded_unverified','Suspension status unknown'
    try:stamp=datetime.fromisoformat(str(snap.get('update_time',''))).date().isoformat()
    except (ValueError,TypeError):return 'excluded_unverified','Snapshot timestamp missing or invalid'
    if stamp<expected:return 'excluded_snapshot_stale','Snapshot older than completed session'
    try:price=number(snap.get('last_price'))
    except SafetyError:return 'excluded_unverified','Invalid snapshot price'
    if price<=0:return 'excluded_unverified','Invalid snapshot price'
    # Unfinished future-session prices cannot pre-exclude a completed-session stock.
    if stamp==expected and price<5:return 'excluded_price','Completed-session price below USD 5'
    return None

class RollingScan:
    def __init__(self,broker,path,report,now,held_codes=()):
        self.b=broker;self.path=Path(path);self.report=Path(report);self.now=now
        self.path.mkdir(parents=True,exist_ok=True);(self.path/'history').mkdir(exist_ok=True)
        self.day=broker.completed_session(now);self.status={};self.histories={};self.errors={}
        self.held=set(held_codes)
        self.calendar=[r['time'] for r in broker.calendar(now) if r['time']<=self.day][-60:]
        if len(self.calendar)!=60:raise SafetyError('Incomplete exchange calendar')
        file=self.path/('catalog_'+self.day+'.json')
        if file.exists():cat=json.loads(file.read_text())
        else:
            cat=dict(basic={r['code']:r for r in broker.basic()},directory=fetch_directories(self.path/'directory'))
            atomic(file,cat)
        reviews=self.path/'reviewed_etfs.json'
        self.universe=classification(cat['directory'],cat['basic'],json.loads(reviews.read_text()) if reviews.exists() else {},now.date())
        self.basic_count=len(cat['basic'])
        self.codes=sorted(c for c,m in self.universe.items() if m['verified'] and m['lot']==1 and
            m['delisted'] is False and m['exchange'] in {'NYSE','NASDAQ','AMEX','ARCA','BATS'})
        self.entry_codes=set(self.codes)
        self.codes=sorted(self.entry_codes|self.held)
        self.snapfile=self.path/('snapshots_'+self.day+'.json')
        self.snaps=json.loads(self.snapfile.read_text()) if self.snapfile.exists() else {}
    def snapshots(self,codes):
        try:return self.b.snapshots(codes)
        except Exception as exc:
            if not any(s in str(exc) for s in ('未知股票','Unknown stock')):raise
            if len(codes)==1:return [dict(code=codes[0],_unavailable=str(exc))]
            n=len(codes)//2;return self.snapshots(codes[:n])+self.snapshots(codes[n:])
    def save(self,stage):
        selected,signals,rejected=candidates(self.universe,self.histories,self.day)
        for code in self.held:
            try:signals[code]=trend(self.histories[code],self.day)
            except (KeyError,SafetyError):pass
        counts=Counter(v['status'] for v in self.status.values())
        processed=sum(v for k,v in counts.items() if k!='pending')
        complete=bool(self.codes) and processed==len(self.codes) and not self.errors and self.held.issubset(signals) and stage=='finished'
        for code,v in self.status.items():
            if v['status'].startswith('excluded_'):rejected[code]=v['reason']
        result=dict(date=self.day,generated_at=datetime.now(timezone.utc).isoformat(),source='moomoo_subscribed_daily',
            complete=complete,basic_count=self.basic_count,directory_count=len(self.universe),eligible_count=len(self.codes),
            processed_count=processed,history_count=len(self.histories),excluded_count=processed-len(self.histories),
            pending_count=len(self.codes)-processed,status_counts=dict(counts),candidates=selected,signals=signals,
            rejected=rejected,errors=self.errors,coverage=self.status,
            limitations='Current listing universe only, not survivorship-free backtesting. 120 recent QFQ daily bars; unfinished sessions removed. Data exclusions listed, not fabricated. Unreviewed ETFs excluded.')
        atomic(self.path/'scan_progress.json',result)
        if complete and stage=='finished':
            from .swing_model import snapshot as short_snapshot
            profile_path=self.path.parent/'selection_profile.json'
            profile=json.loads(profile_path.read_text()).get('profile','legacy') if profile_path.exists() else 'legacy'
            atomic(self.path/'short_scan.json',short_snapshot(result,self.histories,profile))
            atomic(self.path/'completed'/(self.day+'.json'),result)
            atomic(self.path/'scan.json',result)
        atomic(self.report,dict(stage=stage,date=self.day,updated_at=result['generated_at'],universe_count=len(self.codes),
            snapshot_count=sum(not s.get('_unavailable') for s in self.snaps.values()),history_count=len(self.histories),
            processed_count=processed,excluded_count=result['excluded_count'],pending_count=result['pending_count'],
            candidate_count=len(selected),strategy_scan_complete=complete,status_counts=dict(counts),errors=self.errors,
            note='Official subscribed K_DAY; historical-symbol quota untouched; no account or orders.'))
        print(json.dumps({k:result[k] for k in ('date','processed_count','eligible_count','history_count','excluded_count','pending_count','complete')})+' '+stage,flush=True)
        return result
    def consume(self,code,bars):
        status,reason=evaluate_bars(bars,self.calendar,self.day)
        if status=='ready':self.histories[code]=bars
        self.status[code]=dict(status=status,reason=reason)
        if code in self.held and status!='ready':self.errors[code]='Held position has no verified exit signal: '+reason
    def run(self):
        self.save('snapshot_inventory')
        for offset in range(0,len(self.codes),200):
            group=[c for c in self.codes[offset:offset+200] if c not in self.snaps]
            if group:
                self.snaps.update({r['code']:r for r in self.snapshots(group)});atomic(self.snapfile,self.snaps)
            self.save('snapshot_inventory')
        pending=[]
        for code in self.codes:
            if code not in self.snaps:self.errors[code]='Snapshot not returned';continue
            snap=self.snaps[code]
            if code in self.universe:self.universe[code]['suspended']=snap.get('suspension')
            exclusion=None if code in self.held else snapshot_exclusion(snap,self.day)
            if exclusion:self.status[code]=dict(status=exclusion[0],reason=exclusion[1]);continue
            file=self.path/'history'/(code+'.json');meta=file.with_suffix('.meta.json')
            # Older files stay intact until a current-session replacement is fetched.
            try:
                bars=json.loads(file.read_text());provenance=json.loads(meta.read_text())
                if bars and bars[-1].get('date')==self.day and provenance.get('session')==self.day and provenance.get('source')=='moomoo_subscribed_daily':
                    self.consume(code,bars);continue
            except (OSError,ValueError):pass
            pending.append(code);self.status[code]=dict(status='pending',reason='Awaiting current-session candles')
        self.save('daily_candles')
        while pending:
            room=min(250,self.b.subscription_room())
            if room<=0:raise SafetyError('Insufficient subscription quota after reserving 20 slots')
            group=pending[:room];self.b.subscribe_daily(group);subscribed=time.monotonic()
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    try:
                        results=pool.map(lambda code:self.b.daily(code,self.day),group)
                        for index,(code,bars) in enumerate(zip(group,results),1):
                            file=self.path/'history'/(code+'.json')
                            if file.exists():
                                old=json.loads(file.read_text())
                                if old and old[-1].get('date')!=self.day:
                                    atomic(self.path/'history_archive'/old[-1]['date']/(code+'.json'),old)
                            atomic(file,bars)
                            atomic(file.with_suffix('.meta.json'),dict(source='moomoo_subscribed_daily',session=self.day,
                                fetched_at=datetime.now(timezone.utc).isoformat(),ktype='K_DAY',autype='QFQ',requested_count=120,extended_time=False))
                            self.consume(code,bars)
                            if index%50==0:self.save('daily_candles')
                    except BaseException:
                        pool.shutdown(wait=True,cancel_futures=True);raise
                self.save('daily_candles')
            finally:
                # Release only our K_DAY subscriptions after the official one-minute minimum.
                remaining=61-(time.monotonic()-subscribed)
                while remaining>0:time.sleep(min(5,remaining));remaining=61-(time.monotonic()-subscribed)
                self.b.unsubscribe_daily(group)
            pending=pending[len(group):]
        return self.save('finished' if not self.errors else 'needs_attention')

def main(argv=None):
    import argparse
    from .desktop_server import ledger
    parser=argparse.ArgumentParser();parser.add_argument('--market-dir',default='data/trading/market');parser.add_argument('--port',type=int,default=11111);parser.add_argument('--db',default='data/trading/paper.sqlite')
    args=parser.parse_args(argv)
    path=Path(args.market_dir);path.mkdir(parents=True,exist_ok=True)
    with (path/'rolling_scan.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SafetyError('Whole-market scanner already running')
        b=DailyBroker(args.port);scanner=None
        try:
            state=ledger(Path(args.db))
            held=[c for c,p in state.get('positions',{}).items() if p['qty']]
            options=ledger(Path('data/trading/options_observer.sqlite'))
            if options.get('position'):held.append(options['position']['underlying'])
            scanner=RollingScan(b,path,Path('data/trading/preparation_scan.json'),datetime.now(timezone.utc),held)
            result=scanner.run();return 0 if result['complete'] else 1
        except Exception as exc:
            if scanner:scanner.errors['_run']=str(exc);scanner.save('needs_attention')
            raise
        finally:b.close()

if __name__=='__main__':raise SystemExit(main())
