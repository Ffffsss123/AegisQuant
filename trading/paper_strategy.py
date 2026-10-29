"""Automatic handoff to a strictly SIMULATE-only strategy worker."""
import fcntl,time
from datetime import datetime,timezone
from pathlib import Path
from .paper_probe import PaperOnly
from .engine import Engine,TERMINAL
from .store import Store
from .simulation_snapshot import SnapshotBusy
from .policy import Config,SafetyError
from .capital import config_for_state
from .moomoo import NY
from .rolling_scan import atomic
from .desktop_server import ROOT,ledger,read_json
from .validation import protection_passed
from .report import weekly_report

def probe_ready(s):
    orders=list(s.get('orders',{}).values())
    return (s.get('env')=='SIMULATE' and s.get('purpose')=='transport_probe' and
        s.get('probe_phase')=='complete' and not s.get('paused') and not s.get('stopped') and not s.get('alerts') and
        not any(p['qty'] for p in s.get('positions',{}).values()) and bool(s.get('marks')) and
        all(o['status'] in TERMINAL and o.get('fee_final') for o in orders) and
        any(o['status']=='CANCELLED_ALL' and o.get('cancel_requested') for o in orders) and
        all(any(o['side']==side and o.get('booked_qty',0)>0 for o in orders) for side in ('BUY','SELL')))

def handoff(b,db,probe,now):
    account=probe.get('account')
    if not account or probe.get('env')!='SIMULATE' or probe.get('purpose')!='transport_probe':raise SafetyError('Unexpected probe identity')
    b.bind(account,'SIMULATE')
    if db.exists():
        s=db.load()
        if s.get('account')!=account or s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation':
            raise SafetyError('Strategy account/purpose mismatch; ledger cannot be reset')
        Engine(b,db,config_for_state(s)).load();return s
    if not probe_ready(probe):raise SafetyError('Interface acceptance or reconciliation incomplete')
    remote=b.orders(probe['bound_at'][:10]);known={o.get('id'):o for o in probe['orders'].values()}
    if any(b.positions().values()):raise SafetyError('Simulation account not flat after probe')
    if any(o['status'] not in TERMINAL or o['id'] not in known for o in remote):
        raise SafetyError('Unresolved or external simulation orders prevent handoff')
    by_id={o['id']:o for o in remote}
    for oid,o in known.items():
        r=by_id.get(oid)
        if not r or r['status']!=o['status'] or r['code']!=o['code'] or r['side']!=o['side'] or abs(r['qty']-o['qty'])>1e-8 or abs(r['filled']-o['booked_qty'])>1e-8:
            raise SafetyError('Probe broker/local order mismatch')
    e=Engine(b,db,Config())
    # Identity and entry delay are committed in the same initial transaction.
    s=e.bind(account,'SIMULATE',.6,now,extra=dict(purpose='strategy_simulation',entry_after_session=now.astimezone(NY).date().isoformat(),
        handoff_at=now.isoformat(),probe_account_suffix=str(account)[-4:]))
    return s

def strategy_evidence(s):
    orders=[o for o in s.get('orders',{}).values() if o.get('kind')=='STOCK' and o.get('execution_purpose','strategy')=='strategy']
    if s.get('purpose')!='strategy_simulation' or s.get('env')!='SIMULATE':return False
    if s.get('paused') or s.get('stopped') or s.get('alerts') or not s.get('marks'):return False
    if any(p['qty'] for p in s.get('positions',{}).values()):return False
    if any(o['status'] not in TERMINAL or not o.get('fee_final') for o in s.get('orders',{}).values()):return False
    buys={o['code'] for o in orders if o['side']=='BUY' and o.get('booked_qty',0)>0}
    sells={o['code'] for o in orders if o['side']=='SELL' and o.get('booked_qty',0)>0}
    return bool(buys&sells) and all(o.get('id') for o in orders)

def current_scan(root,b,now):
    market=Path(root)/'data/trading/market';expected=b.completed_session(now)
    complete=read_json(market/'scan.json');progress=read_json(market/'scan_progress.json')
    if complete.get('date')==expected and complete.get('complete'):return complete
    # Current partial signals may service exits, but never authorize entries.
    result=progress if progress.get('date')==expected else dict(date=expected,signals={},candidates=[])
    return dict(result,complete=False)

def report(root,s,reason=None):
    checks=protection_passed(root)
    passed=bool(s.get('strategy_acceptance')) and checks
    status='strategy_passed' if passed else ('stopped' if s.get('stopped') else 'paused' if s.get('paused') else 'strategy_running')
    if reason:status='needs_attention'
    atomic(Path(root)/'data/trading/strategy.report.json',dict(status=status,updated_at=datetime.now(timezone.utc).isoformat(),
        reason=reason,env='SIMULATE',strategy_acceptance=passed,protection_tests_passed=checks,live_enabled=False,
        candidate_fallback_enabled=s.get('candidate_fallback_enabled',False),candidate_fallback_limit=s.get('candidate_fallback_limit'),candidate_checks=s.get('candidate_checks',{}),entry_global_block=s.get('entry_global_block'),strategy_version=s.get('strategy_version','daily_sma_v1'),strategy_parameters=s.get('strategy_parameters'),exit_quote_status=s.get('exit_quote_status',{}),
        entry_timing=s.get('entry_timing','opening_window'),emergency_exits=s.get('emergency_exits',{}),temporary_entry_window=s.get('temporary_entry_window'),entry_after_session=s.get('entry_after_session'),quote_wait=s.get('quote_wait'),data_block=s.get('data_block'),
        orders=len(s.get('orders',{})),fee_note='模拟费用使用接口费用或既有预留规则，不是实盘费用验证'))
    return passed

def bound_hk_loss(root,broker,*,history_start):
    """Called under joint admission lock, after US order maintenance."""
    import json
    from .hk_fx import validate_accounting_binding
    from .hk_readonly import HKReadOnly
    from .hk_risk_producer import collect_bound_loss
    base=Path(root)/'data/trading'
    stock=ledger(base/'paper.sqlite')
    account=stock.get('hk_account')
    if (stock.get('env')!='SIMULATE' or stock.get('capital_mode')!='independent_sleeves_v1'
            or not isinstance(account,str) or not account.isdigit() or int(account)<=0
            or account==str(stock.get('account'))):
        raise SafetyError('Existing independent HK simulation binding required')
    if not history_start or history_start!=stock.get('hk_history_start'):
        raise SafetyError('Verified complete HK history start required')
    from .simulation_history import bound_hk_history_start
    history_start=bound_hk_history_start(stock,account=account,now=datetime.now(timezone.utc))
    fx=json.loads((base/'hk_fx_binding.json').read_text())
    validate_accounting_binding(fx,stock.get('portfolio_id'),account,datetime.now(timezone.utc))
    # Create lazily: missing HK setup cannot prevent preceding US cancellation.
    # This context is never assigned to, or bound through, the US broker.
    import moomoo as sdk
    context=sdk.OpenSecTradeContext(filter_trdmarket=sdk.TrdMarket.HK,
        host='127.0.0.1',port=11111,security_firm=sdk.SecurityFirm.FUTUAU)
    try:
        reader=HKReadOnly(sdk,context,account,base/'hk_loss_evidence.jsonl')
        return collect_bound_loss(root,reader,stock,book_provider=broker.book,history_start=history_start)
    finally:context.close()

def independent_order_maintenance(root,broker,state,*,observe_loss=False,hk_loss_provider=None):
    """Maintenance-only dispatch; no independent entry or budget activation.

    Call outside admission/Store locks. Both ledgers are reloaded and reconciled
    by the joint session, including on the runner's exception path.
    """
    from .simulation_history import load_reset_history
    from .simulation_reconcile import cancel_expired_locked
    if state.get('env')!='SIMULATE' or state.get('capital_mode')!='independent_sleeves_v1':
        raise SafetyError('Unsupported independent maintenance mode')
    base=Path(root)/'data/trading'
    reset=read_json(base/'reset_status.json')
    if (reset.get('status')!='initialized_paused' or not reset.get('archive_complete')
            or reset.get('request_id')!=state.get('acceptance_reset_id')):
        raise SafetyError('Verified acceptance archive required for joint maintenance')
    archive=load_reset_history(reset['archive'],account=state['account'],request_id=reset['request_id'])
    paths={'US_STOCK':base/'paper.sqlite','US_OPTION':base/'option_transport.sqlite'}
    if observe_loss:
        from .simulation_admission import observe_locked_us_loss
        if hk_loss_provider is None:
            hk_loss_provider=lambda:bound_hk_loss(root,broker,history_start=state.get('hk_history_start'))
        # Cancellation precedes HK valuation/risk-readiness checks. Missing
        # binding or risk state must fail closed without suppressing maintenance.
        return observe_locked_us_loss(root,broker,paths,account=state['account'],
            hk_account=state.get('hk_account'),portfolio=state.get('portfolio_id'),
            archive=archive,risk_path=base/'simulation_loss_latch.sqlite',service_orders=True,
            hk_loss_provider=hk_loss_provider)
    return cancel_expired_locked(root,broker,
        paths,
        account=state['account'],archive=archive,honor_controls=True,honor_news=True)

def independent_strategy_cycle(root,broker,state):
    broker.shared_read_budget=True
    from .simulation_history import load_reset_history
    from .independent_stock_cycle import run_once
    from .hk_maintenance import bound_resources
    base=Path(root)/'data/trading';reset=read_json(base/'reset_status.json')
    if (reset.get('status')!='initialized_paused' or not reset.get('archive_complete')
            or reset.get('request_id')!=state.get('acceptance_reset_id')):
        raise SafetyError('Verified acceptance archive required')
    archive=load_reset_history(reset['archive'],account=state['account'],request_id=reset['request_id'])
    # Resolve resources lazily after the initial order maintenance, so an HK
    # connection failure cannot defer cancellation of a US order.
    def refresh():
        with bound_resources(root,state.get('hk_account')) as callback:return callback()
    return run_once(root,broker,{'US_STOCK':base/'paper.sqlite','US_OPTION':base/'option_transport.sqlite'},
        account=state['account'],hk_account=state.get('hk_account'),portfolio=state['portfolio_id'],
        archive=archive,refresh_hk=refresh)

def main():
    path=ROOT/'data/trading/paper.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
    with open(str(path)+'.runner.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        # Never overlap the probe, including the brief gap after its report is written.
        with open(str(ROOT/'data/trading/probe.sqlite')+'.runner.lock','a') as probe_lock:
            try:fcntl.flock(probe_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            b=None;db=Store(path)
            try:
                if not protection_passed(ROOT):raise SafetyError('Protection checks missing or code changed')
                b=PaperOnly();b.audit=db.event
                # Bid valuation is conservative even when new purchases would
                # fail their spread gate. Source freshness remains mandatory.
                b.allow_wide_stock_valuation=True
                from .shared_capital import admission_lock
                with admission_lock(ROOT),db.lock():s=handoff(b,db,ledger(ROOT/'data/trading/probe.sqlite'),datetime.now(timezone.utc))
            except Exception as exc:
                report(ROOT,{},str(exc));db.close()
                if b:b.close()
                return 1
        if s.get('strategy_version')=='swing_1_5_v1':
            from .swing_engine import SwingEngine
            e=SwingEngine(b,db,config_for_state(s))
        else:e=Engine(b,db,config_for_state(s))
        last_report=0
        e.option_state=lambda:ledger(ROOT/'data/trading/options_observer.sqlite')
        e.controls=lambda:read_json(ROOT/'data/trading/simulation_controls.json')
        try:
            while True:
                started=time.monotonic();now=datetime.now(timezone.utc)
                try:
                    s=db.load()
                    if s.get('capital_mode') is not None:
                        independent_strategy_cycle(ROOT,b,s)
                        report(ROOT,db.load())
                        time.sleep(max(0,5-(time.monotonic()-started)))
                        continue  # Native swing decisions use joint entry/exit capabilities.
                    snapshot=current_scan(ROOT,b,now)
                    if s.get('strategy_version')=='swing_1_5_v1':
                        short=read_json(ROOT/'data/trading/market/short_scan.json')
                        matches=short.get('version')==s['strategy_version'] and short.get('date')==snapshot.get('date') and short.get('source_generated_at')==snapshot.get('generated_at')
                        snapshot=dict(short,entry_verified_codes=[c for c in snapshot.get('signals',{}) if c not in snapshot.get('rejected',{})]) if matches else dict(date=snapshot.get('date'),version=s['strategy_version'],complete=False,signals={},candidates=[])
                    from .news_selection import opportunity_pool,extend_us_scan
                    snapshot=extend_us_scan(snapshot,opportunity_pool(read_json(ROOT/'data/trading/news/latest.json'),now))
                    from .shared_capital import admission_lock
                    with admission_lock(ROOT),db.lock():
                        s=db.load();controls=e.controls()
                        if controls.get('paused') or controls.get('stopped'):
                            s['paused']=True
                            if controls.get('stopped'):s['stopped']=True
                            db.save(s,'simulation_control')
                        from .option_transport import active
                        if controls.get('reset_pending'):
                            from .reset_liquidation import step as reset_step
                            reset_step(e,s,now,ROOT)
                            report(ROOT,db.load())
                        elif active(ROOT):
                            s['data_block']='期权接口验收暂时独占模拟账户，股票等待对账完成'
                            db.save(s,'option_transport_wait');report(ROOT,s)
                        else:
                            e.tick(snapshot,now)
                            s=db.load()
                            # Observe a clean natural exit before considering another entry.
                            if strategy_evidence(s) and protection_passed(ROOT) and not s.get('data_block') and not s.get('strategy_acceptance'):
                                s['strategy_acceptance']=True;s.setdefault('strategy_accepted_at',now.isoformat());db.save(s,'strategy_acceptance')
                            if not e.blocked_orders(s):e.drain(snapshot,datetime.now(timezone.utc))
                            s=db.load();report(ROOT,s)
                    if started-last_report>=60:
                        weekly_report(s,ROOT/'data/trading/reports');last_report=started
                except SnapshotBusy:
                    report(ROOT,db.load(),'Waiting for cooperating ledger writer')
                except Exception as exc:
                    with db.lock():
                        s=db.load();e.alert(s,'Strategy runtime error: '+str(exc))
                        if s.get('capital_mode') is None:
                            try:e.manage_orders(s,datetime.now(timezone.utc))
                            except Exception:pass
                        report(ROOT,s,str(exc))
                    if s.get('capital_mode') is not None:
                        try:independent_order_maintenance(ROOT,b,s,observe_loss=True)
                        except Exception:pass
                time.sleep(max(0,5-(time.monotonic()-started)))
        finally:b.close();db.close()

if __name__=='__main__':raise SystemExit(main())
