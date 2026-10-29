"""Real broker SIMULATE option transport test, isolated from strategy evidence."""
import fcntl,time,math
from datetime import datetime,timezone
from pathlib import Path
from .paper_probe import PaperOnly,ProbeStep
from .quote_feed import QuoteUnavailable
from .read_retry import ReadQueryTimeout
from .engine import Engine,TERMINAL
from .policy import Config,SafetyError,quote_price,option_candidates
from .store import Store
from .simulation_snapshot import SnapshotBusy,locked_ledgers
from .desktop_server import ROOT,read_json,ledger
from .rolling_scan import atomic
from .shared_capital import admission_lock,available_cash
from .capital import config_for_state
from .validation import protection_passed
from .options_observer import contract as normalize_contract

CONTROL='option_transport_control.json'
GATE='option_transport_gate.json'
REPORT='option_transport.report.json'

def active(root=ROOT):
    base=Path(root)/'data/trading';gate=read_json(base/GATE)
    if gate.get('active'):return True
    # A missing gate file must not silently release an initialized test account.
    return bool(ledger(base/'option_transport.sqlite')) and not bool(gate.get('completed_at'))

def ready_for_reservation(stock,options,controls):
    from .capital import require_no_pending_migration
    require_no_pending_migration(controls)
    if stock.get('capital_mode') is not None or options.get('capital_mode') is not None:
        raise SafetyError('Independent budgets cannot use legacy option reservation')
    if stock.get('env')!='SIMULATE' or stock.get('purpose')!='strategy_simulation':raise SafetyError('Explicit strategy simulation binding required')
    if stock.get('paused') or stock.get('stopped') or controls.get('paused') or controls.get('stopped'):raise SafetyError('User or risk pause is preserved')
    if any(p['qty'] for p in stock['positions'].values()) or stock.get('queue'):raise SafetyError('Wait for stock strategy to be flat and idle')
    if any(o['status'] not in TERMINAL for o in stock['orders'].values()) or options.get('position'):raise SafetyError('Wait for pending orders or paper option positions')
    cash=available_cash(stock,options)
    equity=stock['cash']+options['cash']-options['initial']
    from .hk_shared_admission import exposure
    equity+=exposure(stock)['total_net_pnl_usd']
    loss=max(0,stock['initial']-equity)
    if cash<=10 or loss>=stock['loss_limit']:raise SafetyError('Insufficient shared budget or risk room')
    return cash,min(stock['loss_limit']-loss,cash)

class OptionStep(ProbeStep):
    def __init__(self,engine,contract):super().__init__(engine,contract['code']);self.contract=contract
    def submit(self,s,side,qty,price,phase,now):
        old_phase=s['probe_phase'];old_orders=set(s['orders'])
        s['probe_phase']=phase;self.e.store.save(s,'probe_phase_claim')
        try:return self.e.submit(s,self.code,side,qty,price,now,'OPTION',self.contract,execution_purpose='option_transport_probe')
        except SafetyError as exc:
            if set(s['orders'])!=old_orders:raise SafetyError('Failure after durable intent; reconcile before retry') from exc
            s['probe_phase']=old_phase;self.e.store.save(s,'probe_submission_deferred')
            if str(exc) in ('Stale/future quote','Spread too wide'):raise QuoteUnavailable(str(exc),'waiting_quote') from exc
            raise
    def tick(self,s,now):
        if s.get('paused') or s.get('stopped') or s.get('query_retry',{}).get('exhausted'):
            self.e.manage_orders(s,now)
            return 'needs_attention'
        return super().tick(s,now)
    def advance_tick(self,s,now):
        # Keep servicing cancellation even after pause/error; never clear the gate on error.
        self.e.manage_orders(s,now)
        if s.get('paused') or s.get('stopped'):return 'needs_attention'
        if any(p['qty'] for p in s['positions'].values()):
            self.e.mark(s,now)
            if s['paused']:return 'needs_attention'
        return super().advance_tick(s,now)
    def advance(self,s,now):
        e=self.e;b=e.b;phase=s['probe_phase'];owned=s['positions'].get(self.code,{}).get('qty',0)
        orders=list(s['orders'].values())
        if phase in ('cancel_test','buy','sell'):
            if phase in ('cancel_test','buy') and hasattr(self,'validate_entry'):
                self.contract=self.validate_entry(s,now)
            book=b.book(self.code)
            try:price=quote_price(book,'SELL' if phase=='sell' else 'BUY',b.now(),True)
            except SafetyError as exc:
                if str(exc) in ('Stale/future quote','Spread too wide'):raise QuoteUnavailable(str(exc),'waiting_quote') from exc
                raise
            if phase=='cancel_test':
                tick=self.contract['tick_size'];price=round(math.floor(book['bid']*.5/tick)*tick,8)
                if price<=0 or price>=book['bid']:raise SafetyError('Cannot construct a valid unmarketable cancellation test')
                self.submit(s,'BUY',1,price,'cancel_wait',now)
            elif phase=='buy':
                if price*100+2*e.c.option_fee_reserve_usd>s['cash']+1e-8:raise SafetyError('Option roundtrip exceeds reserved shared cash')
                self.submit(s,'BUY',1,price,'buy_wait',now)
            else:self.submit(s,'SELL',owned,price,'sell_wait',now)
        elif phase=='cancel_wait':
            if owned:raise SafetyError('Cancellation order unexpectedly filled; keep gate and inspect position')
            if not orders or orders[-1]['status']!='CANCELLED_ALL' or not orders[-1].get('cancel_requested'):raise SafetyError('Cancellation not verified')
            s['probe_phase']='buy';e.store.save(s,'probe_phase')
        elif phase=='buy_wait':
            if owned!=1:raise SafetyError('Option buy not filled; no repricing or replacement')
            s['probe_phase']='sell';e.store.save(s,'probe_phase')
        elif phase=='sell_wait':
            if owned:raise SafetyError('Option exit unfilled; inspection required')
            e.mark(s,now);s['probe_phase']='complete';e.store.save(s,'probe_complete')
            return 'transport_passed'
        else:raise SafetyError('Unknown option probe phase')
        return 'running'

def passed(s):
    if s.get('env')!='SIMULATE' or s.get('purpose')!='option_transport_probe' or s.get('probe_phase')!='complete' or s.get('paused') or s.get('stopped') or s.get('alerts'):return False
    rows=list(s.get('orders',{}).values())
    if len(rows)!=3 or any(p['qty'] for p in s['positions'].values()):return False
    if any(not o.get('id') or o['kind']!='OPTION' or o['status'] not in TERMINAL or not o.get('fee_final') for o in rows):return False
    return (rows[0]['side']=='BUY' and rows[0]['status']=='CANCELLED_ALL' and rows[0].get('cancel_requested') and rows[0]['booked_qty']==0
            and rows[1]['side']=='BUY' and rows[1]['booked_qty']==1 and rows[2]['side']=='SELL' and rows[2]['booked_qty']==1)

def report(status,s=None,reason=None):
    s=s or {}
    atomic(ROOT/'data/trading'/REPORT,dict(status=status,reason=reason,updated_at=datetime.now(timezone.utc).isoformat(),
        env='SIMULATE',purpose='option_transport_probe',code=s.get('probe_code'),phase=s.get('probe_phase'),
        transport_passed=passed(s),strategy_acceptance=False,live_enabled=False,
        orders=[{k:o.get(k) for k in ('id','code','side','qty','status','booked_qty','booked_fee','fee_final')} for o in s.get('orders',{}).values()],
        position_qty=sum(p['qty'] for p in s.get('positions',{}).values()),quote_wait=s.get('quote_wait'),alerts=s.get('alerts',[]),
        fee_note='券商模拟费用或单边 5 USD 预留；不是实盘收费验证'))

def verify_flat_evidence(b,s):
    if not passed(s):raise SafetyError('Incomplete transport evidence')
    rows=b.orders(s['bound_at'][:10]);remote={o['id']:o for o in rows}
    if any(b.positions().values()) or any(o['status'] not in TERMINAL for o in rows):raise SafetyError('Broker not flat and idle after test')
    allowed=set(s.get('reserved_known_ids',[]))|{o['id'] for o in s['orders'].values()}
    if any(o['id'] not in allowed for o in rows):raise SafetyError('Unknown broker activity during transport test')
    for local in s['orders'].values():
        r=remote.get(local['id'])
        if not r or any(r.get(k)!=local.get(k) for k in ('code','side','qty','status')) or r.get('filled')!=local['booked_qty']:
            raise SafetyError('Final broker evidence mismatch')

def verify_independent_evidence(root,b,paths,*,account,portfolio,archive,record_completion=False):
    """Verify completed option transport against the full owned account union.

    Staged verifier only; legacy flat verification remains unchanged. No entry,
    exclusive-gate release is authorized by this result. Optional completion
    records only local P&L metadata while the verified account locks are held.
    """
    from .simulation_reconcile import account_session
    if set(paths)!={'US_STOCK','US_OPTION'} or not portfolio:
        raise SafetyError('Explicit independent execution ledgers required')
    with account_session(root,b,paths,account=account,archive=archive) as (states,evidence,engines):
        for sleeve,state in states.items():
            if (state.get('capital_mode')!='independent_sleeves_v1' or state.get('sleeve')!=sleeve
                    or state.get('portfolio_id')!=portfolio or state.get('shared_capital') is not False
                    or state.get('budget_currency')!='USD' or state.get('initial')!=500):
                raise SafetyError('Independent completion identity mismatch')
        if not passed(states['US_OPTION']):raise SafetyError('Incomplete independent option transport evidence')
        if record_completion:
            option=states['US_OPTION'];cash=option.get('cash')
            if isinstance(cash,bool) or not isinstance(cash,(int,float)) or not math.isfinite(cash) or cash<0:
                raise SafetyError('Invalid independent completion cash')
            prior=option.get('independent_settlement')
            if prior and (prior.get('identity')!=option['bound_at']
                    or prior.get('transferred_to_stock_usd')!=0
                    or prior.get('net_pnl_usd')!=cash-option['initial']):
                raise SafetyError('Independent completion record mismatch')
            if not prior:
                option['independent_settlement']=dict(identity=option['bound_at'],
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    net_pnl_usd=cash-option['initial'],transferred_to_stock_usd=0)
                engines['US_OPTION'].store.save(option,'independent_option_transport_completion',option['independent_settlement'])
        result=dict(account_evidence=evidence,option_transport_passed=True,
                    strategy_acceptance=False,entry_enabled=False,gate_released=False)
        engines['US_OPTION'].store.event('independent_option_evidence_verified',result)
        return result


def settle(db,stock_db,now,root=ROOT):
    s=db.load();stock=stock_db.load()
    if not passed(s):raise SafetyError('Cannot settle incomplete option transport test')
    if s['account']!=stock['account'] or stock['env']!='SIMULATE':raise SafetyError('Settlement account mismatch')
    if any(x.get('capital_mode') not in (None, 'independent_sleeves_v1') for x in (s, stock)):
        raise SafetyError('Unsupported capital mode; no shared settlement fallback')
    independent = s.get('capital_mode') == 'independent_sleeves_v1'
    if independent or stock.get('capital_mode') == 'independent_sleeves_v1':
        if (not independent or stock.get('capital_mode') != 'independent_sleeves_v1'
                or s.get('sleeve') != 'US_OPTION' or stock.get('sleeve') != 'US_STOCK'
                or not s.get('portfolio_id') or s['portfolio_id'] != stock.get('portfolio_id')
                or any(x.get('initial') != 500 or x.get('budget_currency') != 'USD' for x in (s, stock))):
            raise SafetyError('Independent settlement identity mismatch')
        if isinstance(s.get('cash'), bool) or not isinstance(s.get('cash'), (int,float)) or not math.isfinite(s['cash']) or s['cash'] < 0:
            raise SafetyError('Invalid independent settlement cash')
        # Fills and fees already changed only this sleeve's cash. Completion is
        # idempotent metadata; never transfer its P&L into the stock ledger.
        if not s.get('independent_settlement'):
            s['independent_settlement'] = dict(identity=s['bound_at'], completed_at=now.isoformat(),
                net_pnl_usd=s['cash']-s['initial'], transferred_to_stock_usd=0)
            db.save(s, 'independent_option_transport_completion', s['independent_settlement'])
    else:
        identity=s['bound_at'];applied=stock.setdefault('option_transport_settlements',[])
        if identity not in applied:
            delta=s['cash']-s['initial'];stock['cash']+=delta;applied.append(identity)
            stock.setdefault('capital_changes',[]).append(dict(time=now.isoformat(),old_initial=stock['initial'],new_initial=stock['initial'],amount_usd=delta,reason='isolated_option_transport_result_not_strategy_pnl'))
            stock['option_transport_pnl_usd']=stock.get('option_transport_pnl_usd',0)+delta
            stock_db.save(stock,'option_transport_settlement',dict(delta_usd=delta,identity=identity))
    atomic(Path(root)/'data/trading'/GATE,dict(active=False,completed_at=now.isoformat()))
    request=read_json(Path(root)/'data/trading'/CONTROL);request.update(requested=False,completed_at=now.isoformat());atomic(Path(root)/'data/trading'/CONTROL,request)

class OptionBroker(PaperOnly):
    def mark(self,code,now):return quote_price(self.book(code),'SELL',self.now(),True)

def fresh_offer(offer,now):
    try:return bool(offer) and 0<=(now-datetime.fromisoformat(offer['created_at'])).total_seconds()<=120
    except (KeyError,ValueError,TypeError):return False

def validate_contract(b,old,now,*,scan,available_cash):
    """Read-only contract revalidation with explicit scan and sleeve cash."""
    from zoneinfo import ZoneInfo
    if isinstance(available_cash,bool):raise SafetyError('Invalid option sleeve cash')
    try:available_cash=float(available_cash)
    except (ValueError,TypeError,OverflowError) as exc:
        raise SafetyError('Invalid option sleeve cash') from exc
    if not math.isfinite(available_cash) or available_cash<=10:
        raise SafetyError('Insufficient finite option sleeve cash including roundtrip fees')
    code=old['code']
    chain=next(x for x in b.option_chain(old['underlying'],now.astimezone(ZoneInfo('America/New_York')).date()) if x['code']==code)
    snapshot=b.snapshots([code])[0];c=normalize_contract(chain,snapshot,b.now())
    c['tick_size']=float(snapshot['price_spread'])
    if not math.isfinite(c['tick_size']) or c['tick_size']<=0:raise SafetyError('Option tick size missing')
    c['book']=b.book(code)
    session=b.execution_session(b.now(),strict=False)
    if not session or not scan.get('complete') or scan.get('date')!=session[1]:raise SafetyError('Current complete scan required')
    if not option_candidates([c],scan['signals'],now.astimezone(ZoneInfo('America/New_York')).date(),b.now(),available_cash-5,5):raise SafetyError('Option no longer meets entry rules')
    return c

def prepare_independent_contract(root,broker,paths,*,account,hk_account,portfolio,
                                 archive,risk_path,contract,required_usd,refresh_hk=None):
    """Read-only locked admission plus contract preparation; never a send permit."""
    from .simulation_admission import admitted_us_session
    started=datetime.now(timezone.utc)
    with admitted_us_session(root,broker,paths,account=account,hk_account=hk_account,
            portfolio=portfolio,archive=archive,risk_path=risk_path,
            sleeve='US_OPTION',required_usd=required_usd,refresh_hk=refresh_hk) as (_,admission,engines):
        try:
            scan=read_json(Path(root)/'data/trading/market/scan.json')
            # Require the full roundtrip to fit the exact admitted request, not
            # merely the remaining sleeve balance. No budget reservation is implied.
            decision=engines['US_OPTION'].news(contract['underlying'],datetime.now(timezone.utc))
            engines['US_OPTION'].store.event('independent_option_news_decision',decision)
            factor=decision.get('multiplier')
            if (isinstance(factor,bool) or not isinstance(factor,(int,float))
                    or not math.isfinite(factor) or not 0<factor<=1 or decision.get('global_block')):
                raise SafetyError('News risk filter blocks independent option preparation')
            from decimal import Decimal
            cap=min(admission['available_usd']*Decimal(str(factor)),admission['required_usd'])
            verified=validate_contract(broker,contract,datetime.now(timezone.utc),
                                       scan=scan,available_cash=cap)
            controls=engines['US_OPTION'].controls()
            if any(controls.get(flag) is not False for flag in ('paused','stopped','reset_pending')):
                raise SafetyError('Option preparation controls changed or unavailable')
            if verified.get('underlying')!=contract['underlying']:
                raise SafetyError('Verified option underlying changed')
            state=engines['US_OPTION'].store.load()
            completed=any(o.get('side')=='BUY' and o.get('booked_qty',0)>0 and o.get('entry_news_evidence') for o in state['orders'].values())
            if controls.get('require_news_opportunity') and not completed:
                if verified.get('right')!='CALL' or not decision.get('entry_evidence'):
                    raise SafetyError('Verified positive news opportunity required before option preparation')
            if not 0<=(datetime.now(timezone.utc)-started).total_seconds()<=30:
                raise SafetyError('Independent option preparation expired')
        except Exception as exc:
            engines['US_OPTION'].store.event('independent_option_contract_rejected',
                dict(code=contract.get('code'),required_usd=str(required_usd),
                     error_type=type(exc).__name__,reason=str(exc),submission_enabled=False))
            raise
        result=dict(contract=verified,admission=admission,news=decision,submission_enabled=False,
                    prepared_at=datetime.now(timezone.utc).isoformat())
        engines['US_OPTION'].store.event('independent_option_contract_prepared',
                                       dict(code=verified['code'],required_usd=str(required_usd),submission_enabled=False))
        return result

def validate_entry(b,s,now,*,root=None):
    base=Path(root) if root is not None else ROOT
    scan=read_json(base/'data/trading/market/scan.json')
    return validate_contract(b,s['probe_contract'],now,scan=scan,available_cash=s['cash'])

def controls_now():
    controls=read_json(ROOT/'data/trading/simulation_controls.json')
    deadline=read_json(ROOT/'data/trading'/CONTROL).get('expires_at')
    if deadline and datetime.now(timezone.utc)>=datetime.fromisoformat(deadline):controls['paused']=True
    return controls

def verify_initial_account(b, stock, probe):
    """Keep the flat-account gate while recognizing verified pre-reset history."""
    from .simulation_history import load_reset_history, reconcile_with_history
    from .simulation_ownership import reconcile_orders, reconcile_positions
    states = {'stock': stock, 'probe': probe}
    archive = None
    if stock.get('acceptance_reset_id'):
        archive = load_reset_history(Path(stock['historical_order_manifest']).parent,
            account=stock['account'], request_id=stock['acceptance_reset_id'])
    start = min(s['bound_at'][:10] for s in states.values())
    if archive: start = min(start, archive['history_start'])
    remote = b.orders(start)
    if archive: reconcile_with_history(states, stock['account'], remote, archive)
    else: reconcile_orders(states, stock['account'], remote)
    positions = b.positions()
    reconcile_positions(states, stock['account'], [dict(code=k, qty=v) for k,v in positions.items()])
    if any(positions.values()) or any(o['status'] not in TERMINAL for o in remote):
        raise SafetyError('Broker account not flat/idle')
    return {o['id'] for o in remote}

def submit_confirmed_option_exit(engine,now):
    """Submit only a persisted sell phase inside an existing scoped joint session.

    Caller owns the admission and ledger locks. Paused buying stays paused;
    Engine retains session, quote, stopped-control and inventory checks.
    This helper neither grants transport permission nor retries an exit.
    """
    state=engine.store.load()
    if (state.get('env')!='SIMULATE' or state.get('purpose')!='option_transport_probe'
            or state.get('probe_phase')!='sell'):
        raise SafetyError('Confirmed option sell phase required')
    contract=state.get('probe_contract',{})
    code=contract.get('code')
    if (not code or state.get('probe_code')!=code
            or state.get('positions',{}).get(code,{}).get('qty')!=1):
        raise SafetyError('Confirmed owned option contract required')
    if not hasattr(engine,'before_order_intent'):
        raise SafetyError('Scoped joint exit session required')
    # Do not claim a new phase until pending/unknown orders and fees are settled.
    if engine.blocked_orders(state):raise SafetyError('Pending option exit reconciliation')
    book=engine.b.book(code)
    price=quote_price(book,'SELL',engine.b.now(),True)
    tag=OptionStep(engine,contract).submit(state,'SELL',1,price,'sell_wait',now)
    order=engine.store.load()['orders'][tag]
    return dict(status='sell_wait',tag=tag,order_status=order['status'],
                acknowledged=bool(order.get('id')) and order['status']=='SUBMITTED')

def confirmed_option_exit_cycle(root,broker,paths,*,account,archive,owner='US_OPTION'):
    """Explicit staged exit service; no production routing or entry permission.

    Cancel/reconcile first, then acquire a new complete joint snapshot for exit.
    Never call while holding an admission or ledger lock.
    """
    from .simulation_reconcile import cancel_expired_locked,account_session
    maintenance=cancel_expired_locked(root,broker,paths,account=account,archive=archive,
                                      honor_controls=True,honor_news=True)
    with account_session(root,broker,paths,account=account,archive=archive,
                         option_exit_owner=owner) as (_,evidence,engines):
        engine=engines[owner];state=engine.store.load()
        if state.get('probe_phase')!='sell':
            return dict(status='maintenance_only',maintenance=maintenance,evidence=evidence)
        result=submit_confirmed_option_exit(engine,datetime.now(timezone.utc))
        return dict(result,maintenance=maintenance,evidence=evidence)

def independent_maintenance(root,broker,state):
    """Route existing independent ledgers to joint maintenance, never entries.

    Called without ledger locks; the shared coordinator acquires all locks.
    Contract initialization and the legacy exclusive gate are not used here.
    """
    from .paper_strategy import independent_order_maintenance
    stock=ledger(Path(root)/'data/trading/paper.sqlite')
    if (state.get('env')!='SIMULATE' or state.get('capital_mode')!='independent_sleeves_v1'
            or state.get('account')!=stock.get('account')):
        raise SafetyError('Independent option maintenance identity mismatch')
    return independent_order_maintenance(root,broker,stock)

def independent_execution_cycle(root,broker,state):
    broker.shared_read_budget=True
    from .simulation_history import load_reset_history
    from .independent_option_cycle import run_once
    from .hk_maintenance import bound_resources
    base=Path(root)/'data/trading';stock=ledger(base/'paper.sqlite');reset=read_json(base/'reset_status.json')
    if (reset.get('status')!='initialized_paused' or not reset.get('archive_complete')
            or reset.get('request_id')!=stock.get('acceptance_reset_id')):
        raise SafetyError('Verified acceptance archive required')
    archive=load_reset_history(reset['archive'],account=stock['account'],request_id=reset['request_id'])
    def refresh():
        with bound_resources(root,stock.get('hk_account')) as callback:return callback()
    return run_once(root,broker,{'US_STOCK':base/'paper.sqlite','US_OPTION':base/'option_transport.sqlite'},
        account=stock['account'],hk_account=stock.get('hk_account'),portfolio=stock['portfolio_id'],
        archive=archive,refresh_hk=refresh)

def main():
    path=ROOT/'data/trading/option_transport.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
    with open(str(path)+'.runner.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        db=Store(path);b=None;e=None;step=None;pending_error=None
        try:
            while True:
                now=datetime.now(timezone.utc)
                try:
                    if pending_error is not None:raise pending_error
                    if not protection_passed(ROOT):raise SafetyError('Protection validation missing or code changed')
                    if not db.exists():
                        request=read_json(ROOT/'data/trading'/CONTROL)
                        if not request.get('requested'):return
                        if request.get('expires_at') and now>=datetime.fromisoformat(request['expires_at']):
                            report('needs_attention',reason='本次验收授权时段结束，未强制选择不合格合约');return
                        offer=read_json(ROOT/'data/trading/options_broker_candidate.json')
                        if not fresh_offer(offer,now):
                            report('waiting_qualified_contract');time.sleep(5);continue
                        if b is None:b=OptionBroker();b.audit=db.event
                        if not b.execution_session(now,strict=False):report('waiting_regular_session');time.sleep(5);continue
                        # Requery metadata, snapshots and books; the offer is not execution proof.
                        code=offer['contract']['code'];underlying=offer['contract']['underlying']
                        chain=next(x for x in b.option_chain(underlying,b.now().date()) if x['code']==code)
                        snapshot=b.snapshots([code])[0];c=normalize_contract(chain,snapshot,b.now());c['tick_size']=float(snapshot['price_spread'])
                        if not math.isfinite(c['tick_size']) or not c['tick_size']>0:raise SafetyError('Option tick size missing')
                        c['book']=b.book(code)
                        with admission_lock(ROOT):
                            stock=ledger(ROOT/'data/trading/paper.sqlite');options=ledger(ROOT/'data/trading/options_observer.sqlite');controls=read_json(ROOT/'data/trading/simulation_controls.json')
                            cash,loss=ready_for_reservation(stock,options,controls)
                            scan=read_json(ROOT/'data/trading/market/scan.json')
                            session=b.execution_session(b.now(),strict=False)
                            if not session or scan.get('date')!=session[1] or not scan.get('complete'):raise SafetyError('Current complete scan required')
                            if not option_candidates([c],scan['signals'],b.now().date(),b.now(),cash-5,5):raise SafetyError('Candidate no longer passes option rules')
                            b.bind(stock['account'],'SIMULATE')
                            known=verify_initial_account(b,stock,ledger(ROOT/'data/trading/probe.sqlite'))
                            cfg=Config(budget_usd=cash,loss_limit_usd=loss,option_cap_usd=cash,shared_capital=True,allocation=1)
                            e=Engine(b,db,cfg)
                            # Reserve the account before initializing; crashes keep the gate closed.
                            gate=read_json(ROOT/'data/trading'/GATE)
                            if gate.get('active') and str(gate.get('account'))!=stock['account']:raise SafetyError('Another account owns the exclusive gate')
                            atomic(ROOT/'data/trading'/GATE,dict(active=True,account=stock['account'],started_at=now.isoformat()))
                            with db.lock():
                                s=e.bind(stock['account'],'SIMULATE',stock['fx'],now,extra=dict(reserved_known_ids=sorted(known-{None}),shared_capital=True,purpose='option_transport_probe',probe_phase='cancel_test',probe_code=code,probe_contract=c))
                    if b is None:b=OptionBroker();b.audit=db.event
                    if db.load().get('capital_mode') is not None:
                        result=independent_execution_cycle(ROOT,b,db.load())
                        report(result['status'],db.load())
                        time.sleep(5);continue
                    if e is None:e=Engine(b,db,config_for_state(db.load()))
                    e.controls=controls_now
                    if step is None:
                        step=OptionStep(e,db.load()['probe_contract'])
                        step.validate_entry=lambda state,stamp:validate_entry(b,state,stamp)
                    with admission_lock(ROOT),db.lock():
                        s=e.load();stock=ledger(ROOT/'data/trading/paper.sqlite')
                        if s['account']!=stock['account']:raise SafetyError('Bound account changed')
                        if not read_json(ROOT/'data/trading'/GATE).get('active') and not passed(s):raise SafetyError('Exclusive test gate missing')
                        gate=read_json(ROOT/'data/trading'/GATE)
                        if gate.get('active') and str(gate.get('account'))!=s['account']:raise SafetyError('Exclusive gate account mismatch')
                        status=step.tick(s,b.now());report(status,s)
                        if status=='transport_passed':
                            verify_flat_evidence(b,s)
                            sd=Store(ROOT/'data/trading/paper.sqlite')
                            try:
                                with sd.lock():settle(db,sd,b.now())
                            finally:sd.close()
                            return
                        # Remain alive to service unresolved orders; no new buys after failure.
                except SnapshotBusy:
                    report('waiting_reconciliation',db.load())
                except Exception as exc:
                    if db.exists():
                        # Preserve the original fault until its pause/alert is durable.
                        # Joint maintenance owns this same file lock; contention is
                        # not another fatal exception and must not permit entry.
                        pending_error=exc
                        try:
                            with admission_lock(ROOT),locked_ledgers({'US_OPTION':path}):
                                s=db.load();e=Engine(b,db,config_for_state(s)) if b else None
                                s['paused']=True
                                if str(exc) not in s['alerts']:s['alerts'].append(str(exc))
                                db.save(s,'option_transport_error')
                                pending_error=None
                                if e and s.get('capital_mode') is None:
                                    try:e.load();e.manage_orders(s,datetime.now(timezone.utc))
                                    except Exception:pass
                                report('needs_attention',s,str(exc))
                        except SnapshotBusy:
                            report('waiting_error_checkpoint',db.load(),str(exc))
                            time.sleep(5);continue
                        if b and s.get('capital_mode') is not None:
                            try:independent_maintenance(ROOT,b,s)
                            except Exception:pass
                        time.sleep(5);continue
                    report('candidate_validation_wait',reason=str(exc))
                time.sleep(5)
        finally:
            if b:b.close()
            db.close()

if __name__=='__main__':main()
