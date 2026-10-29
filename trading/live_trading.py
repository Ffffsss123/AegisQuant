"""Opt-in US/HK cash-stock execution. Separate ledgers, no live auto-start."""
import argparse, fcntl, hashlib, json, os, sqlite3, time, tempfile, threading
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .policy import Config, SafetyError, number, whole_qty, eligible
from .engine import Engine, TERMINAL
from .store import Store
from .moomoo import NY
from .hk_policy import HK, board_order
from .news import NewsGuard

CONFIRM='ENABLE LIVE STOCKS'


def read(path):
    return json.loads(Path(path).read_text())


def private_write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent);tmp=Path(name)
    with os.fdopen(fd,'w') as f:
        json.dump(value,f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


@contextmanager
def portfolio_lock(root):
    folder=Path(root)/'data/trading/live';folder.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(folder,0o700)
    with (folder/'portfolio.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield folder
        finally:fcntl.flock(f,fcntl.LOCK_UN)


def template(root):
    path=Path(root)/'data/trading/live/config.json'
    if path.exists():raise SafetyError('Live configuration already exists; never overwritten')
    private_write(path,dict(version=1,env='REAL',port=11111,firm='FUTUAU',
        reserve_usd=390,max_positions=2,joint_loss_limit_usd=None,
        automatic_entries=False,markets={
            'US':dict(account='',budget_native=None,loss_native=None,fee_reserve_native=3),
            'HK':dict(account='',budget_native=None,loss_native=None,fee_reserve_native=100)},
        note='Fill in your own account IDs and cash budgets. Native currencies: US=USD, HK=HKD. No funding or FX conversion is performed.'))
    return path


def config(root,now,allow_stale_fx=False):
    folder=Path(root)/'data/trading/live';c=read(folder/'config.json')
    if c.get('version')!=1 or c.get('env')!='REAL' or c.get('max_positions')!=2:
        raise SafetyError('Version 1 REAL stock portfolio with two joint slots required')
    if type(c.get('automatic_entries')) is not bool or type(c.get('port')) is not int or not 1<=c['port']<=65535:
        raise SafetyError('Explicit automatic-entry choice and local gateway port required')
    if c.get('firm') not in ('FUTUAU','FUTUINC','FUTUSECURITIES','FUTUSG'):
        raise SafetyError('Unsupported securities entity')
    if not isinstance(c.get('markets'),dict) or not c['markets'] or set(c['markets'])-{'US','HK'}:
        raise SafetyError('Only US/HK cash stocks supported; options and futures disabled')
    reserve=number(c.get('reserve_usd'))
    if reserve<390:raise SafetyError('USD 390 reserve must remain untouched')
    joint=number(c.get('joint_loss_limit_usd'))
    if not 0<joint<=100:raise SafetyError('Explicit positive joint loss limit up to USD 100 required')
    rates={'US':1.}
    if 'HK' in c['markets']:
        from .hk_fx import validate_reference
        try:rates['HK']=float(validate_reference(read(folder/'fx.json'),now))
        except (OSError,ValueError,KeyError,SafetyError):
            if not allow_stale_fx:raise
            rates['HK']=number(read(folder/'controls.json')['bound_rates']['HK'])
            if rates['HK']<=0:raise SafetyError('Invalid bound FX reference')
    for market,spec in c['markets'].items():
        if not isinstance(spec.get('account'),str) or not spec['account'].isdigit() or int(spec['account'])<=0:
            raise SafetyError('Fill in your own explicit REAL account ID for '+market)
        b,l,fee=[number(spec.get(k)) for k in ('budget_native','loss_native','fee_reserve_native')]
        if not 0<l<=b or fee<(3 if market=='US' else 20):raise SafetyError('Invalid live native-currency budget, loss or fee reserve')
    total=sum(number(s['budget_native'])/rates[m] for m,s in c['markets'].items())
    if not joint<=total<=890:raise SafetyError('Combined explicit live budget must cover loss limit and be at most USD 890')
    # FX data is not part of configuration identity; native budgets never grow.
    digest=hashlib.sha256(json.dumps(c,sort_keys=True).encode()).hexdigest()
    return c,rates,digest


@dataclass(frozen=True)
class NativePolicy(Config):
    """The reused engine operates in ONE account's native currency, not USD."""
    def validate(self):
        if self.live_stocks is not True or self.live_options or self.shared_capital:
            raise SafetyError('Live policy is cash stocks only')
        if not 0<self.loss_limit_usd<=self.budget_usd or self.fee_reserve_usd<=0:
            raise SafetyError('Invalid native-currency risk limits')
        return self


def policy(spec):
    return NativePolicy(budget_usd=number(spec['budget_native']),loss_limit_usd=number(spec['loss_native']),
        option_cap_usd=1,fee_reserve_usd=number(spec['fee_reserve_native']),
        live_stocks=True,live_options=False,max_positions=2,allocation=.45)


def simulation_evidence(root,market,now):
    """Read evidence; never mark simulation accepted or change its controls."""
    root=Path(root);base=root/'data/trading'
    from .desktop_server import ledger
    if market=='US':
        s=ledger(base/'paper.sqlite')
        if (s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation'
            or s.get('strategy_acceptance') is not True or s.get('paused') or s.get('stopped') or s.get('alerts')
            or any(p['qty'] for p in s['positions'].values()) or not s.get('marks')):
            raise SafetyError('US natural strategy simulation acceptance must be complete, flat and healthy')
        natural={}
        for o in s.get('orders',{}).values():
            if o['status'] not in TERMINAL or not o.get('fee_final'):raise SafetyError('Unresolved paper orders or fees')
            if o.get('execution_purpose')=='strategy' and o.get('kind')=='STOCK' and o.get('booked_qty',0)>0:
                d=natural.setdefault(o['code'],{'BUY':0,'SELL':0});d[o['side']]+=o['booked_qty']
        if not any(d['BUY']>0 and d['BUY']==d['SELL'] for d in natural.values()):
            raise SafetyError('Natural simulated buy/sell round trip required; reset and probe exits excluded')
        return dict(market=market,source='paper.sqlite',accepted=True)
    # The existing HK simulator has separate submission and settlement journals.
    risk=read(base/'hk_risk_snapshot.json');s=ledger(base/'paper.sqlite')
    stamp=datetime.fromisoformat(risk['as_of'])
    if (risk.get('env')!='SIMULATE' or risk.get('reconciled') is not True
        or risk.get('account')!=s.get('hk_account') or risk.get('portfolio_id')!=s.get('portfolio_id')
        or risk.get('pending_or_unknown') is not False or risk.get('entry_block') is not False
        or number(risk.get('occupied_usd'))!=0 or stamp.tzinfo is None
        or not 0<=(now-stamp).total_seconds()<=30):
        raise SafetyError('Fresh flat HK simulation reconciliation required')
    with sqlite3.connect((base/'hk_reservations.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('SELECT portfolio FROM identity WHERE id=1').fetchone()!=(s.get('portfolio_id'),):
            raise SafetyError('HK evidence portfolio mismatch')
        if db.execute("SELECT 1 FROM holds WHERE state!='RELEASED'").fetchone():raise SafetyError('HK simulation holds unresolved')
        closed=db.execute('SELECT key,qty,order_ids,evidence FROM closed_positions').fetchall()
    with sqlite3.connect((base/'hk_submissions.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('SELECT account FROM binding WHERE id=1').fetchone()!=(risk['account'],):
            raise SafetyError('HK evidence account mismatch')
        submissions={tag:(json.loads(body),oid) for tag,body,oid in db.execute('SELECT tag,request,order_id FROM submissions')}
    with sqlite3.connect((base/'hk_runtime.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        natural_exits={row.get('tag') for (body,) in db.execute("SELECT body FROM events WHERE kind='hk_exit_decision'")
                       for row in [json.loads(body)] if row.get('reason') in ('trend_reversal','emergency_news')}
    completed=False
    for key,qty,ids,body in closed:
        if key not in submissions:continue
        entry,oid=submissions[key];exits=json.loads(ids)
        matching=[request for tag,(request,order_id) in submissions.items()
                  if tag in natural_exits and order_id in exits and request.get('side')=='SELL'
                  and request.get('position_key')==key and request.get('code')==entry.get('code')]
        if (number(qty)>0 and json.loads(body).get('inventory_as_of') and entry.get('side')=='BUY'
            and entry.get('env')=='SIMULATE' and entry.get('account')==risk['account'] and matching):completed=True
    if not completed:raise SafetyError('Natural settled HK strategy round trip required; transport/reset exits excluded')
    return dict(market=market,source='HK submission and settlement journals',accepted=True)


class LiveEngine(Engine):
    def __init__(self,broker,store,p,root,market,guard):
        super().__init__(broker,store,p,NewsGuard(Path(root)/'data/trading/news/latest.json'))
        self.root=Path(root);self.market=market;self.before_order_intent=guard
    def load(self):
        s=super().load()
        if s.get('env')!='REAL' or s.get('purpose')!='live_cash_stocks_v1' or s.get('market')!=self.market:
            raise SafetyError('Separate live stock ledger required')
        return s
    def mark(self,s,now):
        value=s['cash']
        for code,p in s['positions'].items():
            if p['qty']:value+=p['qty']*self.b.mark(code,self.clock(now))
        if s['initial']-value>=s['loss_limit']:self.alert(s,'Live native-currency loss limit latched')
        s['live_mark']=dict(time=self.clock(now).isoformat(),equity_native=value)
        self.store.save(s,'live_valuation',s['live_mark']);return value
    def submit(self,s,code,side,qty,price,now,kind='STOCK',**kwargs):
        if kind!='STOCK' or not code.startswith(self.market+'.'):
            raise SafetyError('Only the bound market cash stocks are executable')
        info=self.b.stock_metadata(code,qty,price)
        if self.market=='HK':
            from .hk_metadata import execution_metadata
            from .hk_fees import order_upper_bound
            market=self.root/'data/trading/hk_market'
            meta=execution_metadata(market,read(market/'catalog.json'),info['snapshot'],info['checked_at'],code,price,self.clock(now))
            board_order(code,qty,price,meta['lot_size'],meta['tick_size'])
            if float(order_upper_bound(qty*price,qty)['total_hkd'])>self.c.fee_reserve_usd:
                raise SafetyError('HK fee reserve insufficient for execution rounding')
        elif side=='BUY':
            from .scanner import classification
            market=self.root/'data/trading/market';day=self.b.completed_session(self.clock(now))
            catalog=read(market/('catalog_'+day+'.json'))
            reviewed=read(market/'reviewed_etfs.json') if (market/'reviewed_etfs.json').exists() else {}
            universe=classification(catalog['directory'],catalog['basic'],reviewed,self.clock(now).date())
            if not eligible(universe.get(code,{})) or universe[code]['kind']!='COMMON':
                raise SafetyError('Verified common stock required')
        return super().submit(s,code,side,qty,price,now,kind=kind,**kwargs)
    def order_quantity(self,s,item,price,factor):
        qty=super().order_quantity(s,item,price,factor)
        if self.market=='HK' and item['side']=='BUY' and qty:
            lot=self.b.stock_metadata(item['code'],0,price)['lot'];qty=qty//lot*lot
        return qty


def require_worker(root):
    """Orders require a live process holding the runner lock and a fresh lease."""
    folder=Path(root)/'data/trading/live'
    from .desktop_server import alive
    from .validation import fingerprint,protection_passed
    try:
        if not protection_passed(root):raise SafetyError('Current protection regression required before live submission')
        lease=read(folder/'heartbeat.json');stamp=datetime.fromisoformat(lease['time'])
        if stamp.tzinfo is None or not 0<=(datetime.now(timezone.utc)-stamp).total_seconds()<=30:
            raise SafetyError('Live maintenance heartbeat expired')
        if lease.get('fingerprint')!=fingerprint(root):raise SafetyError('Live worker source changed; restart maintenance after validation')
        if not alive(lease['pid'],'-m trading.live_trading run'):raise SafetyError('Live maintenance worker is not running')
        with (folder/'runner.lock').open('a') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            finally:
                # Closing an unlocked descriptor does not affect the other process.
                pass
        raise SafetyError('Live maintenance runner lock is not held')
    except (OSError,ValueError,KeyError) as exc:
        raise SafetyError('Start the live maintenance worker before submitting orders') from exc


class Portfolio:
    def __init__(self,root,broker_factory=None):
        self.root=Path(root);self.folder=self.root/'data/trading/live'
        from .validation import fingerprint
        self.implementation_fingerprint=fingerprint(root)
        self.c,self.rates,self.digest=config(root,datetime.now(timezone.utc),allow_stale_fx=True)
        if broker_factory is None:
            from .live_broker import LiveBroker
            broker_factory=LiveBroker
        self.brokers={};self.stores={};self.engines={}
        try:
            for m,spec in self.c['markets'].items():
                b=broker_factory(m,spec['account'],self.c['port'],self.c['firm'])
                self.folder.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.folder,0o700)
                b.before_live_send=self.transport_guard
                self.brokers[m]=b;db=Store(self.folder/(m.lower()+'.sqlite'));self.stores[m]=db
                os.chmod(db.path,0o600)
                b.audit=lambda event,body,db=db:db.event(event,body)
                self.engines[m]=LiveEngine(b,db,policy(spec),root,m,self.guard)
        except Exception:self.close();raise
    def close(self):
        for b in self.brokers.values():b.close()
        for db in self.stores.values():db.close()
    def controls(self):
        s=read(self.folder/'controls.json')
        if s.get('config_digest')!=self.digest:raise SafetyError('Live configuration changed; original binding retained')
        latch_path=self.folder/'pause.json'
        if latch_path.exists():
            latch=read(latch_path);s['paused']=True;s['stopped']=bool(s.get('stopped') or latch.get('stopped'))
        return s
    def bind(self):
        config(self.root,datetime.now(timezone.utc))
        if (self.folder/'controls.json').exists():
            raise SafetyError('Live binding already exists; budgets and identities cannot be reset')
        for m,db in self.stores.items():
            if db.exists():
                old=self.engines[m].load()
                if old.get('enabled_stocks') or old.get('enabled_options') or old.get('orders') or any(p['qty'] for p in old['positions'].values()):
                    raise SafetyError('Partial preparation has activity; preserve and inspect the ledger')
        for m in self.engines:simulation_evidence(self.root,m,datetime.now(timezone.utc))
        # Validate every account before any ledger is bound.
        for m,b in self.brokers.items():
            spec=self.c['markets'][m];reserve=self.c['reserve_usd'] if m=='US' else 0
            if b.settled_cash()<spec['budget_native']+reserve:raise SafetyError('Insufficient settled '+m+' cash including reserve')
            if any(o['status'] not in TERMINAL for o in b.orders()):raise SafetyError('Resolve existing live orders before binding')
        for m,e in self.engines.items():
            if self.stores[m].exists():
                s=e.load();s['paused']=True;self.stores[m].save(s,'live_prepare_recovered');continue
            s=e.bind(self.c['markets'][m]['account'],'REAL',1,datetime.now(timezone.utc),
                extra=dict(purpose='live_cash_stocks_v1',market=m,currency='USD' if m=='US' else 'HKD',
                           budget_currency='USD' if m=='US' else 'HKD'))
            s['paused']=True;self.stores[m].save(s,'live_prepared_disabled')
        private_write(self.folder/'controls.json',dict(config_digest=self.digest,enabled=False,paused=True,
            stopped=False,loss_latched=False,bound_rates=self.rates,created_at=datetime.now(timezone.utc).isoformat()))
    def refresh(self,allow_partial=False):
        states={};errors=[]
        for m,e in self.engines.items():
            try:
                s=e.load();self.stores[m].event('live_reconcile_start',{'market':m})
                e.reconcile(s,datetime.now(timezone.utc))
                # Deadline cancellations precede quotes and valuation, even on errors.
                e.manage_orders(s,datetime.now(timezone.utc),reconciled=True)
                e.mark(s,datetime.now(timezone.utc))
                if s.pop('live_maintenance_error',None) is not None:self.stores[m].save(s,'live_maintenance_recovered')
                states[m]=s
            except Exception as exc:
                errors.append(m+': '+str(exc))
                try:
                    if self.stores[m].exists():
                        s=self.stores[m].load();s['live_maintenance_error']=str(exc)
                        e.alert(s,'Live maintenance: '+str(exc));states[m]=s
                except Exception:errors.append(m+': ledger unavailable; evidence retained')
        if errors and not allow_partial:raise SafetyError('; '.join(errors))
        return states
    def aggregate(self,states,now):
        ctrl=self.controls();loss=0;slots=set();pending=False
        if set(states)!=set(self.engines):raise SafetyError('Both configured markets must be reconciled')
        for m,s in states.items():
            mark=s.get('live_mark',{});stamp=datetime.fromisoformat(mark['time'])
            if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=30:
                raise SafetyError('Fresh joint live valuations required')
            rate=min(self.rates[m],ctrl['bound_rates'][m])
            loss+=max(0,s['initial']-mark['equity_native'])/rate
            for code,p in s['positions'].items():
                if p['qty']:slots.add((m,code))
            for o in s['orders'].values():
                if o['status'] not in TERMINAL:
                    pending=True
                    if o['side']=='BUY':slots.add((m,o['code']))
            if any(not o.get('fee_final') for o in s['orders'].values()):pending=True
        if loss>=self.c['joint_loss_limit_usd']:
            ctrl.update(loss_latched=True,paused=True);private_write(self.folder/'controls.json',ctrl)
            for m,s in states.items():self.engines[m].alert(s,'Joint live loss limit latched')
        return dict(loss_usd=loss,occupied_slots=len(slots),pending=pending)
    def protection_current(self):
        from .validation import fingerprint,protection_passed
        return protection_passed(self.root) and fingerprint(self.root)==self.implementation_fingerprint
    def require_protection(self):
        if not self.protection_current():raise SafetyError('Code changed or protection validation missing; no new live submission')
    def transport_guard(self,side):
        self.require_protection()
        require_worker(self.root);ctrl=self.controls()
        if not ctrl.get('enabled') or ctrl.get('stopped') or (side=='BUY' and (ctrl.get('paused') or ctrl.get('loss_latched'))):
            raise SafetyError('Live controls changed before transport')
    def guard(self,s,order,tag):
        self.require_protection()
        require_worker(self.root)
        ctrl=self.controls();now=datetime.now(timezone.utc)
        if not ctrl.get('enabled') or ctrl.get('stopped'):raise SafetyError('Live execution is not armed')
        if order['side']=='SELL':return  # Engine already checks owned holdings, quote and session.
        if ctrl.get('paused') or ctrl.get('loss_latched'):raise SafetyError('Live buys paused or loss-latched')
        _,_,current_digest=config(self.root,now)
        if current_digest!=self.digest:raise SafetyError('Live config changed; entry permission is invalid')
        states={m:db.load() for m,db in self.stores.items()};view=self.aggregate(states,now)
        if view['pending'] or view['occupied_slots']>=2 or any(v['paused'] or v['stopped'] for v in states.values()):
            raise SafetyError('Joint order, position or pause state blocks live buying')
        if view['loss_usd']>=self.c['joint_loss_limit_usd']:raise SafetyError('Joint loss limit reached')
        # Reserve stays in existing USD cash; no transfer, borrowing or conversion.
        m=s['market'];cash=self.brokers[m].settled_cash()
        reserve=self.c['reserve_usd'] if m=='US' else 0
        if order['qty']*order['price']+order['fee_reserve']>cash-reserve:
            raise SafetyError('Settled cash after reserve is insufficient')
    def enable(self,confirmation):
        if confirmation!=CONFIRM:raise SafetyError('Type '+CONFIRM+' to enable real stock trading')
        config(self.root,datetime.now(timezone.utc))
        latch_path=self.folder/'pause.json';initial_latch=read(latch_path) if latch_path.exists() else None
        self.require_protection()
        ctrl=self.controls()
        if ctrl.get('loss_latched') or ctrl.get('stopped'):raise SafetyError('Latched live stop/loss requires review; cannot clear here')
        for m in self.engines:simulation_evidence(self.root,m,datetime.now(timezone.utc))
        states=self.refresh();view=self.aggregate(states,datetime.now(timezone.utc))
        if view['pending'] or any(s.get('alerts') or s['stopped'] for s in states.values()):
            raise SafetyError('Unresolved live alerts, fees or orders block enable')
        with pause_lock(self.root):
            latest=read(latch_path) if latch_path.exists() else None
            if latest!=initial_latch:raise SafetyError('A newer pause request takes precedence over enable')
            for m,s in states.items():
                s.update(enabled_stocks=True,enabled_options=False,paused=False,
                         stocks_enabled_at=s.get('stocks_enabled_at') or datetime.now(timezone.utc).isoformat())
                self.stores[m].save(s,'live_enabled_explicitly')
            ctrl.update(enabled=True,paused=False);private_write(self.folder/'controls.json',ctrl)
            if latch_path.exists():
                private_write(self.folder/('pause_archived_'+latest['id']+'.json'),latest)
                latch_path.unlink()
    def pause(self,stop=False):
        ctrl=self.controls();ctrl['paused']=True
        ctrl['stopped']=bool(stop or ctrl.get('stopped'));private_write(self.folder/'controls.json',ctrl)
        for m,db in self.stores.items():
            s=db.load();s['paused']=True
            if stop:s['stopped']=True
            db.save(s,'live_stop' if stop else 'live_pause')
    def cycle(self,allow_decisions=True):
        ctrl=self.controls();issues=[]
        if ctrl.get('paused') or ctrl.get('stopped'):
            for m,db in self.stores.items():
                state=db.load();state['paused']=True;state['stopped']=bool(ctrl.get('stopped') or state.get('stopped'))
                db.save(state,'live_control_observed')
        states=self.refresh(allow_partial=True)
        issues.extend(m+' maintenance unavailable' for m,s in states.items() if s.get('live_maintenance_error'))
        if issues:pause_local(self.root)
        try:self.aggregate(states,datetime.now(timezone.utc))
        except (SafetyError,KeyError,ValueError):
            pause_local(self.root);issues.append('Joint valuation unavailable; new buys paused')
        try:
            current,rates,digest=config(self.root,datetime.now(timezone.utc))
            if digest!=self.digest:raise SafetyError('Live configuration changed while running')
            self.c=current;self.rates=rates
        except (OSError,ValueError,KeyError,SafetyError):
            pause_local(self.root);issues.append('Entry configuration or FX unavailable')
        if not self.protection_current():issues.append('Current protection regression or worker restart required')
        if not allow_decisions or not ctrl.get('enabled') or self.controls().get('stopped') or not self.protection_current():
            return dict(status='needs_attention' if issues else 'running',issues=issues)
        for m,e in self.engines.items():
            if m not in states or states[m].get('live_maintenance_error'):continue
            path=self.root/('data/trading/market/scan.json' if m=='US' else 'data/trading/hk_market/scan.json')
            try:
                scan=read(path) if path.exists() else {}
                if m=='HK' and scan:
                    from .hk_signals import current_signals
                    try:current_signals(scan,e.b.calendar(e.b.now()),e.b.now())
                    except (SafetyError,KeyError,ValueError):
                        scan=dict(date=scan.get('date'),complete=False,signals={},candidates=[])
                if not self.c['automatic_entries'] or self.controls().get('paused'):
                    scan=dict(scan,complete=False)
                # A different market's valuation fault blocks new buys but does
                # not bypass or block this market's strict owned-position exit.
                e.tick(scan,datetime.now(timezone.utc));e.drain(scan,datetime.now(timezone.utc))
            except Exception as exc:
                pause_local(self.root);state=self.stores[m].load()
                e.alert(state,'Live execution: '+str(exc));issues.append(m+' execution unavailable')
        return dict(status='needs_attention' if issues else 'running',issues=issues)
    def order(self,market,code,side,qty,limit,confirmation,request_id):
        import re
        if confirmation!='PLACE LIVE ORDER':raise SafetyError('Type PLACE LIVE ORDER to submit a real order')
        if market not in self.engines or side not in ('BUY','SELL') or not re.fullmatch(r'[a-f0-9]{32}',request_id):
            raise SafetyError('Invalid live ticket identity')
        ctrl=self.controls();digest=hashlib.sha256(json.dumps([market,code,side,qty,limit],sort_keys=True).encode()).hexdigest()
        if request_id in ctrl.get('requests',{}):raise SafetyError('Request already claimed; inspect orders, never resubmit')
        states=self.refresh(allow_partial=side=='SELL')
        if side=='SELL' and (set(states)!=set(self.engines) or any(v.get('live_maintenance_error') for v in states.values())):
            pause_local(self.root)
        if side=='BUY':self.aggregate(states,datetime.now(timezone.utc))
        if market not in states:raise SafetyError('Owned market ledger unavailable; inspect retained evidence')
        e=self.engines[market];s=states[market];now=datetime.now(timezone.utc)
        if s.get('live_maintenance_error'):raise SafetyError('Owned market must reconcile before an exit')
        if side=='BUY':
            snapshot=read(self.root/('data/trading/market/scan.json' if market=='US' else 'data/trading/hk_market/scan.json'))
            session=e.b.execution_session(now,strict=False)
            if (not session or snapshot.get('complete') is not True or snapshot.get('date')!=session[1]
                or snapshot.get('signals',{}).get(code,{}).get('buy') is not True
                or snapshot.get('signals',{}).get(code,{}).get('liquid') is not True):
                raise SafetyError('Complete prior-session scan and qualified long stock signal required')
        ctrl=self.controls();ctrl.setdefault('requests',{})[request_id]=dict(digest=digest,claimed_at=now.isoformat())
        private_write(self.folder/'controls.json',ctrl)
        return e.submit(s,code,side,number(qty),number(limit),now,execution_purpose='live_stock_ticket')


@contextmanager
def pause_lock(root):
    folder=Path(root)/'data/trading/live';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'pause.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)


def pause_local(root,stop=False):
    """Immediate latch; independent of broker I/O and the portfolio worker lock."""
    from uuid import uuid4
    path=Path(root)/'data/trading/live/pause.json'
    with pause_lock(root):
        old=read(path) if path.exists() else {}
        private_write(path,dict(id=uuid4().hex,paused=True,stopped=bool(stop or old.get('stopped')),
                               requested_at=datetime.now(timezone.utc).isoformat()))


def public_status(root):
    """No account IDs, client request IDs or raw broker replies are returned to the GUI."""
    from .desktop_server import ledger
    folder=Path(root)/'data/trading/live'
    ctrl=read(folder/'controls.json') if (folder/'controls.json').exists() else {}
    if (folder/'pause.json').exists():
        latch=read(folder/'pause.json');ctrl['paused']=True;ctrl['stopped']=bool(ctrl.get('stopped') or latch.get('stopped'))
    rows=[]
    for m in ('US','HK'):
        s=ledger(folder/(m.lower()+'.sqlite'))
        if not s:continue
        rows.append(dict(market=m,currency=s.get('currency'),budget_native=s.get('initial'),
            enabled=s.get('enabled_stocks',False),paused=s.get('paused',True),stopped=s.get('stopped',False),
            positions=[dict(code=c,qty=p['qty']) for c,p in s.get('positions',{}).items() if p['qty']],
            pending=sum(o['status'] not in TERMINAL for o in s.get('orders',{}).values()),
            orders=[dict(code=o['code'],side=o['side'],qty=o['qty'],filled=o.get('booked_qty',0),
                         limit=o['price'],status=o['status'],cancel_requested=o.get('cancel_requested',False),
                         fee_final=o.get('fee_final',False)) for o in list(s.get('orders',{}).values())[-10:]],
            valuation=s.get('live_mark'),alerts=len(s.get('alerts',[]))))
    return dict(configured=(folder/'config.json').exists(),prepared=bool(ctrl),enabled=ctrl.get('enabled',False),
        paused=ctrl.get('paused',True),stopped=ctrl.get('stopped',False),loss_latched=ctrl.get('loss_latched',False),
        markets=rows,report=read(folder/'report.json') if (folder/'report.json').exists() else {})


def write_report(root,status,message):
    # Gateway error text can contain identifiers. Keep details in private audit/logs.
    private_write(Path(root)/'data/trading/live/report.json',dict(status=status,message=message,
        checked_at=datetime.now(timezone.utc).isoformat()))


def execute(root,request,broker_factory=None):
    action=request.get('action')
    if action in ('pause','stop'):
        pause_local(root,action=='stop');return
    with portfolio_lock(root) as folder:
        if action=='template':template(root);return
        if action=='validate':
            import unittest
            from .validation import fingerprint
            before=fingerprint(root)
            suite=unittest.defaultTestLoader.discover(str(Path(root)/'tests'))
            result=unittest.TextTestRunner(verbosity=1).run(suite)
            passed=result.wasSuccessful() and result.testsRun>=163 and fingerprint(root)==before
            private_write(Path(root)/'data/trading/protection_validation.json',dict(passed=passed,tests=result.testsRun,
                fingerprint=before,completed_at=datetime.now(timezone.utc).isoformat(),command='python -m trading.live_trading validate'))
            if not passed:raise SafetyError('Protection tests failed or source changed during validation')
            return
        if action=='refresh_fx':
            from urllib.request import urlopen
            from .hk_fx import ECB,parse_ecb
            with urlopen(ECB,timeout=15) as response:raw=response.read(100001)
            private_write(folder/'fx.json',parse_ecb(raw,datetime.now(timezone.utc)));return
        if action not in ('prepare','enable','maintain','order'):
            raise SafetyError('Unsupported live operation')
        with ExitStack() as stack:
            p=Portfolio(root,broker_factory);stack.callback(p.close)
            for db in p.stores.values():stack.enter_context(db.lock())
            if action=='prepare':p.bind()
            elif action=='enable':p.enable(request.get('confirmation'))
            elif action=='maintain':return p.cycle(allow_decisions=False)
            else:p.order(request.get('market'),request.get('code'),request.get('side'),
                         request.get('qty'),request.get('limit'),request.get('confirmation'),request.get('request_id'))


@contextmanager
def worker_heartbeat(folder,interval=5,code_fingerprint=None):
    """Process liveness is independent of SDK latency; risk freshness is separate."""
    folder=Path(folder);stopped=threading.Event();errors=[]
    def publish():
        private_write(folder/'heartbeat.json',dict(pid=os.getpid(),time=datetime.now(timezone.utc).isoformat(),fingerprint=code_fingerprint))
    publish()
    def beat():
        while not stopped.wait(interval):
            try:publish()
            except Exception:
                # An expired lease prevents every subsequent physical submission.
                errors.append(True);return
    thread=threading.Thread(target=beat,name='live-worker-heartbeat',daemon=True);thread.start()
    try:yield errors
    finally:stopped.set();thread.join(timeout=1)


def main(argv=None):
    parser=argparse.ArgumentParser(description='AegisQuant US/HK cash-stock live execution; disabled by default')
    parser.add_argument('command',choices=['template','validate','refresh-fx','prepare','status','enable','pause','stop','maintain','run','request'])
    parser.add_argument('--confirmation',default='');parser.add_argument('--request-file',type=Path)
    args=parser.parse_args(argv);root=Path(__file__).resolve().parents[1];folder=root/'data/trading/live'
    if args.command=='status':print(json.dumps(public_status(root),indent=2));return 0
    if args.command=='run':
        folder.mkdir(parents=True,exist_ok=True)
        with (folder/'runner.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # No SDK sessions until the user has explicitly armed live stocks.
            if not read(folder/'controls.json').get('enabled'):raise SafetyError('Enable live stocks explicitly before starting a worker')
            portfolio=Portfolio(root)
            try:
                with worker_heartbeat(folder,code_fingerprint=portfolio.implementation_fingerprint):
                    while True:
                        try:
                            with portfolio_lock(root),ExitStack() as stack:
                                for db in portfolio.stores.values():stack.enter_context(db.lock())
                                result=portfolio.cycle()
                            write_report(root,result['status'],
                                'Live maintenance needs review; new buys paused. See private logs.' if result['issues']
                                else 'Live maintenance cycle completed; see ledgers for order state')
                        except BlockingIOError:pass
                        except Exception:
                            try:pause_local(root)
                            except Exception:pass
                            write_report(root,'needs_attention','Live entries paused. Inspect the private live worker log and ledger evidence.')
                            import traceback;traceback.print_exc()
                        time.sleep(5)
            finally:portfolio.close()
    request=dict(action=args.command.replace('-','_'),confirmation=args.confirmation)
    if args.command=='request':
        file=args.request_file
        if file is None or file.resolve().parent!=(folder/'requests').resolve():raise SafetyError('Invalid local request file')
        request=read(file)
    try:
        result=execute(root,request)
        if isinstance(result,dict) and result.get('status')=='needs_attention':
            write_report(root,'needs_attention','Maintenance incomplete; new buys paused. Inspect private logs and per-market alerts.')
            print('Maintenance needs attention; existing evidence and pauses retained.');return 1
        write_report(root,'completed','Live operation completed: '+request['action'])
        print('Completed. Run status to inspect live state.');return 0
    except Exception:
        write_report(root,'needs_attention','Operation blocked. Check the private live-operation log; no automatic retry.')
        raise


if __name__=='__main__':raise SystemExit(main())
