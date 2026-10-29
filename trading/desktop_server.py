"""Loopback-only API for native macOS UI. Fixed simulation commands and separately armed live-stock control."""
import json,os,secrets,sqlite3,subprocess,threading,time,fcntl
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .position_display import simulation_details,external_details
from .policy import Config
from .market_capabilities import market_capabilities

ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'data/desktop';PYTHON=ROOT/'.venv/bin/python'
COMMANDS={'live_runner':['-m','trading.live_trading','run'],'live_operation':['-m','trading.live_trading','request'],'hk_execution':['-m','trading.hk_execution'],'hk_scan':['-m','trading.hk_scan'],'hk_catalog':['-m','trading.hk_catalog'],'option_transport':['-m','trading.option_transport'],'options':['-m','trading.options_observer'],'scan':['-m','trading.prepare_scan'],'news':['-m','trading.desktop_news'],
          'probe':['-m','trading.desktop_worker','probe'],'pause':['-m','trading.desktop_worker','pause'],
          'account':['-m','trading.desktop_worker','account'],
          'strategy':['-m','trading.paper_strategy'],'automation':['-m','trading.automation'],
          'stop':['-m','trading.desktop_worker','stop']}

def read_json(path,default=None):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return {} if default is None else default

def ledger(path):
    if not path.exists():return {}
    c=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=1)
    try:
        r=c.execute('select body from state where id=1').fetchone();return json.loads(r[0]) if r else {}
    finally:c.close()

def alive(pid,needle):
    try:
        text=subprocess.check_output(['ps','-p',str(int(pid)),'-o','command='],text=True,stderr=subprocess.DEVNULL)
        return needle in text
    except (OSError,ValueError,subprocess.CalledProcessError):return False

@contextmanager
def jobs_lock():
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'jobs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

class Controller:
    def __init__(self):self.lock=threading.Lock();self.jobs={};self.last_message='';DATA.mkdir(parents=True,exist_ok=True)
    def running(self,key):
        p=self.jobs.get(key)
        if p and p.poll() is None:return True
        saved=read_json(DATA/'jobs.json').get(key,{})
        if saved.get('pid') and key in COMMANDS and alive(saved['pid'],' '.join(COMMANDS[key])):return True
        if key=='news':
            pid=read_json(DATA/'news_process.json').get('pid')
            if pid and alive(pid,'-m trading.desktop_news'):return True
        legacy=read_json(ROOT/'data/trading/preparation_processes.json')
        pair={'scan':('scan_preparation','-m trading.prepare_scan'),'probe':('paper_preparation','-m trading.paper_probe')}.get(key)
        if pair and legacy.get(pair[0]):return alive(legacy[pair[0]],pair[1])
        return False
    def start(self,key):
        if key not in COMMANDS:raise ValueError('Unsupported action')
        with self.lock,jobs_lock():
            if self.running(key):return '任务已经运行，没有重复启动'
            if key=='live_operation':raise ValueError('Use the scoped live operation endpoint')
            if key=='live_runner':
                from .live_trading import public_status
                if not public_status(ROOT).get('enabled'):raise ValueError('Explicit live stock enable required')
            if key=='probe':
                s=ledger(ROOT/'data/trading/probe.sqlite')
                if s.get('env')!='SIMULATE':raise ValueError('尚未绑定模拟验收账户')
                if s.get('probe_phase')=='complete':return '模拟接口验收已完成'
            # All state controlled from this app is restricted to SIMULATE.
            for name in (() if key in ('news','live_runner') else ('paper.sqlite','probe.sqlite')):
                s=ledger(ROOT/'data/trading'/name)
                if s and s.get('env')!='SIMULATE':raise ValueError('检测到真实账户账本；图形界面不会启动该账户')
            log=(DATA/(key+'.log')).open('a')
            try:self.jobs[key]=subprocess.Popen([str(PYTHON),*COMMANDS[key],*(getattr(self,'scan_args',[]) if key=='scan' else [])],cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            finally:log.close()
            if isinstance(self.jobs[key].pid,int):
                saved=read_json(DATA/'jobs.json');saved[key]={'pid':self.jobs[key].pid}
                tmp=DATA/'jobs.tmp';tmp.write_text(json.dumps(saved));tmp.replace(DATA/'jobs.json')
            self.last_message={'live_runner':'Live stock worker started explicitly; inspect Live Trading status','hk_execution':'港股模拟执行协调已启动','hk_scan':'港股行情扫描已启动','hk_catalog':'港股证券目录刷新已启动','option_transport':'期权券商模拟接口验收已启动','options':'期权真实报价纸面模拟已启动','scan':'行情扫描已启动','news':'新闻监测已启动','probe':'模拟验收已启动，请留意状态与错误','pause':'正在暂停模拟开仓并尝试撤单，请等待结果','account':'正在只读刷新真实账户','strategy':'策略模拟正在检查账户并启动','automation':'每日扫描与模拟衔接已启动','stop':'已保存停止指令，正在核对撤单'}[key]
            return self.last_message
    def live_action(self,payload):
        from .live_trading import private_write,pause_local
        action=payload.get('action')
        if action=='start':return self.start('live_runner')
        if action in ('pause','stop'):
            pause_local(ROOT,action=='stop')
            return 'Live pause saved locally; running worker continues cancellation and reconciliation'
        if action not in ('template','validate','refresh_fx','prepare','enable','order','maintain'):
            raise ValueError('Unsupported live action')
        allowed={'action','confirmation','market','code','side','qty','limit','request_id'}
        if set(payload)-allowed:raise ValueError('Unexpected live request fields')
        import uuid
        with self.lock,jobs_lock():
            if self.running('live_operation'):raise ValueError('Live operation already running; inspect status before retry')
            file=ROOT/'data/trading/live/requests'/(uuid.uuid4().hex+'.json')
            private_write(file,payload)
            log=(DATA/'live_operation.log').open('a');os.chmod(DATA/'live_operation.log',0o600)
            try:
                child=subprocess.Popen([str(PYTHON),*COMMANDS['live_operation'],'--request-file',str(file)],
                    cwd=str(ROOT),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            finally:log.close()
            self.jobs['live_operation']=child
            saved=read_json(DATA/'jobs.json');saved['live_operation']={'pid':child.pid}
            private_write(DATA/'jobs.json',saved)
            return 'Live operation queued: '+action+'. Inspect status; never assume a fill from this response.'
    def supervise_news(self,stop):
        # Only public news starts automatically; scanning and orders remain explicit.
        while not stop.is_set():
            try:self.start('news')
            except Exception as exc:self.last_message='新闻自动重试中：'+str(exc)
            stop.wait(30)
    def supervise_automation(self,stop):
        while not stop.is_set():
            try:
                self.start('automation')
                hk=read_json(ROOT/'data/trading/hk_execution.enabled.json')
                stock=ledger(ROOT/'data/trading/paper.sqlite')
                if (hk.get('enabled') is True and hk.get('env')=='SIMULATE'
                        and stock.get('capital_mode')=='independent_sleeves_v1'
                        and hk.get('migration_id')==stock.get('capital_migration_id')
                        and hk.get('portfolio_id')==stock.get('portfolio_id')):
                    self.start('hk_execution')
                if read_json(ROOT/'data/trading/options_observer.enabled.json').get('enabled'):self.start('options')
                if read_json(ROOT/'data/trading/option_transport_control.json').get('requested') and (read_json(ROOT/'data/trading/option_transport.report.json').get('status') not in ('needs_attention','transport_passed') or read_json(ROOT/'data/trading/option_transport_gate.json').get('active')):self.start('option_transport')
            except Exception as exc:self.last_message='自动调度重试中：'+str(exc)
            stop.wait(30)
    def pause_local(self,stop=False):
        for name in ('paper.sqlite','probe.sqlite','option_transport.sqlite'):
            state=ledger(ROOT/'data/trading'/name)
            if state and state.get('env')!='SIMULATE':raise ValueError('拒绝更改真实账户账本')
        from .rolling_scan import atomic
        controls=ROOT/'data/trading/simulation_controls.json'
        from .simulation_controls import update_controls
        update_controls(controls,lambda previous:dict(paused=True,
            stopped=bool(stop or previous.get('stopped')),updated_at=datetime.now(timezone.utc).isoformat()))
        # Persist immediately even if the cancellation worker cannot connect.
        from .store import Store
        for name in ('paper.sqlite','probe.sqlite','option_transport.sqlite'):
            path=ROOT/'data/trading'/name
            if not path.exists():continue
            db=Store(path)
            try:
                if not db.exists():continue
                with db.lock():
                    s=db.load()
                    if s['env']!='SIMULATE':raise ValueError('拒绝更改真实账户账本')
                    s['paused']=True
                    if stop:s['stopped']=True
                    if 'User paused entries' not in s['alerts']:s['alerts'].append('User paused entries')
                    db.save(s,'desktop_pause')
            finally:db.close()
        return self.start('stop' if stop else 'pause')
    def status(self):
        from .alert_history import AlertHistory
        if not hasattr(self,'alert_history'):self.alert_history=AlertHistory()
        scan=read_json(ROOT/'data/trading/market/scan.json');progress=read_json(ROOT/'data/trading/market/scan_progress.json')
        if progress.get('date','')>scan.get('date','') or (progress.get('date')==scan.get('date') and not scan.get('complete')):scan=progress
        prep=read_json(ROOT/'data/trading/preparation_scan.json')
        probe=read_json(ROOT/'data/trading/probe.report.json');paper=ledger(ROOT/'data/trading/paper.sqlite');probe_state=ledger(ROOT/'data/trading/probe.sqlite')
        news=read_json(ROOT/'data/trading/news/latest.json');positions=[]
        short=read_json(ROOT/'data/trading/market/short_scan.json')
        short_active=paper.get('strategy_version')=='swing_1_5_v1'
        short_valid=short.get('version')=='swing_1_5_v1' and short.get('complete') and short.get('date')==scan.get('date') and short.get('source_generated_at')==scan.get('generated_at')
        shown_candidates=(short.get('candidates',[]) if short_valid else []) if short_active else scan.get('candidates',[])
        for name,s in [('策略模拟',paper),('接口测试',probe_state)]:
            for code,v in s.get('positions',{}).items():
                if v.get('qty'):positions.append(dict(code=code,name=name,qty=v['qty'],kind=v.get('kind',''),currency='USD',source='SIMULATE',holding_sessions=v.get('holding_sessions'),swing_stop=v.get('swing_stop'),swing_target=v.get('swing_target'),**simulation_details(s,code,v)))
        option_position=read_json(ROOT/'data/trading/options.report.json').get('position')
        if option_position:positions.append(dict(code=option_position['code'],name='期权报价研究 · '+option_position['underlying'],qty=1,kind='OPTION',currency='USD',source='纸面假设 · 非券商持仓',buy_price=option_position.get('entry_price'),buy_price_label='纸面买入价',position_status='纸面持仓 · 非券商成交'))
        external=read_json(DATA/'account.json')
        for a in external.get('accounts',[]):
            for v in a.get('positions',[]):positions.append(dict(code=v['code'],name=v.get('stock_name',''),qty=v['qty'],currency=v.get('currency',''),market_val=v.get('market_val'),pl_val=v.get('pl_val'),source='已有实盘 · 仅查看',**external_details(v,external.get('updated_at'))))
        from .position_quotes import display_quote
        quote_cache=read_json(DATA/'position_quotes.json')
        for position in positions:position.update(display_quote(quote_cache,position['code']))
        funding=read_json(ROOT/'data/trading/usd_funding_plan.json')
        # Display projection only; execution always reads its own bound ledgers.
        sleeves=[paper,ledger(ROOT/'data/trading/option_transport.sqlite'),ledger(ROOT/'data/trading/hk_runtime.sqlite')]
        independent=bool(paper.get('portfolio_id')) and all(
            s.get('env')=='SIMULATE' and s.get('capital_mode')=='independent_sleeves_v1'
            and s.get('portfolio_id')==paper['portfolio_id'] and s.get('initial')==500 for s in sleeves)
        simulation_total=sum(s['initial'] for s in sleeves) if independent else paper.get('initial',600)
        if independent:
            funding=dict(funding,shared_capital=False,allocation_mode='independent_sleeves_v1',
                         budget_usd=simulation_total,total_usd=simulation_total+paper['reserve_usd'],
                         reserve_usd=paper['reserve_usd'],news_pool_usd=0,news_entry_enabled=True,
                         sleeves_usd={'US_STOCK':500,'US_OPTION':500,'HK_STOCK':500},
                         observer_execution_budget_usd=0)

        marks=paper.get('marks',[])
        from .report import weekly_rows
        from .news import coverage_age_limit
        rows,_=weekly_rows(paper) if paper else ([],0)
        weeks=[dict(week=r['week_start'],equity=r['equity_usd'],pnl=r['pnl_usd'],capital_flow_usd=r['capital_flow_usd'],as_of=r['as_of']) for r in rows]
        errors=[]
        for key,p in self.jobs.items():
            if p.poll() not in (None,0):
                if key.startswith('live_'):
                    errors.append(key+': live operation stopped; inspect private logs');continue
                lines=(DATA/(key+'.log')).read_text(errors='replace').splitlines();errors.append(key+': '+(lines[-1] if lines else '任务退出'))
        sources=[dict(name=k,**v) for k,v in news.get('health',{}).items() if not k.startswith('US.')]
        events=sorted(news.get('events',[]),key=lambda x:x.get('observed_at',''),reverse=True)[:150]
        # No account IDs or credentials exposed to the client.
        from .live_trading import public_status
        live=public_status(ROOT)
        return dict(live=live,market_capabilities=market_capabilities(),updated_at=datetime.now(timezone.utc).isoformat(),live_enabled=live['enabled'],display_currency='USD',funding=funding,simulation_total_budget_usd=simulation_total,independent_budget_display=independent,budget_usd=paper.get('initial',600),loss_limit_usd=paper.get('loss_limit',30),single_risk_usd=min(paper.get('initial',600)*.025,paper.get('loss_limit',30)),
            running={k:self.running(k) for k in COMMANDS},message=self.last_message,scan=dict({k:scan.get(k) for k in ('date','complete','history_count','eligible_count','errors','processed_count','excluded_count','pending_count','status_counts','source')},candidate_count=len(shown_candidates)),
            scan_schedule=read_json(ROOT/'data/trading/scan_schedule.json'),automation=read_json(ROOT/'data/trading/automation.json'),strategy=read_json(ROOT/'data/trading/strategy.report.json'),option_transport=read_json(ROOT/'data/trading/option_transport.report.json'),options=read_json(ROOT/'data/trading/options.report.json'),paper_paused=paper.get('paused',False),
            inventory=prep.get('snapshot_count',0),candidates=shown_candidates[:50],swing_research=read_json(ROOT/'data/trading/swing_research.json'),probe=probe,probe_paused=probe_state.get('paused',False),
            paper_bound=bool(paper),positions=positions,account_updated=external.get('updated_at'),account_error=external.get('error'),
            weeks=weeks,marks=[dict(time=m['time'],equity=m['equity_usd']) for m in marks[-300:]],
            alert_records=self.alert_history.records(ROOT/'data/trading/paper.sqlite',paper.get('alerts',[]),'策略模拟')+self.alert_history.records(ROOT/'data/trading/probe.sqlite',probe_state.get('alerts',[]),'接口测试'),sources=sources,events=events,news_updated=news.get('collected_at'),news_schedule=read_json(ROOT/'data/trading/news_schedule.json'),news_max_age_seconds=coverage_age_limit(news),errors=errors,alerts=paper.get('alerts',[])+probe_state.get('alerts',[]))

class Server(ThreadingHTTPServer):
    daemon_threads=True

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,body):
        data=json.dumps(body,ensure_ascii=False,default=str).encode();self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def authorized(self):
        return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port) and secrets.compare_digest(self.headers.get('X-Local-Token',''),self.server.token) and not self.headers.get('Origin')
    def do_GET(self):
        if not self.authorized():self.reply(403,{'error':'Forbidden'});return
        if self.path!='/status':self.reply(404,{'error':'Not found'});return
        try:self.reply(200,self.server.controller.status())
        except Exception as exc:self.reply(500,{'error':str(exc)})
    def do_POST(self):
        if not self.authorized():self.reply(403,{'error':'Forbidden'});return
        if self.path not in ('/action','/live'):self.reply(404,{'error':'Not found'});return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=4096:raise ValueError('Invalid request length')
            payload=json.loads(self.rfile.read(n))
            if self.path=='/live':
                self.reply(200,{'message':self.server.controller.live_action(payload)});return
            key=payload['action']
            if key in ('pause','stop'):message=self.server.controller.pause_local(stop=key=='stop')
            elif key=='prepare':message=self.server.controller.start('scan')+'；'+self.server.controller.start('news')
            else:message=self.server.controller.start(key)
            self.reply(200,{'message':message})
        except Exception as exc:self.reply(400,{'error':str(exc)})

def main():
    DATA.mkdir(parents=True,exist_ok=True);lock=(DATA/'server.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    server=Server(('127.0.0.1',0),Handler);server.token=secrets.token_hex(32);server.controller=Controller()
    path=DATA/'session.json';fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(dict(port=server.server_port,token=server.token),f)
    stop=threading.Event()
    from .position_quotes import supervise
    threading.Thread(target=supervise,args=(stop,ROOT),daemon=True).start()
    threading.Thread(target=server.controller.supervise_news,args=(stop,),daemon=True).start()
    threading.Thread(target=server.controller.supervise_automation,args=(stop,),daemon=True).start()
    try:server.serve_forever()
    finally:stop.set();server.server_close()

if __name__=='__main__':main()
