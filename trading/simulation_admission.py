"""Independent SIMULATE entry checks; caller holds admission and ledger locks.

Inputs must already be account-reconciled and freshly valued. This module does
not collect evidence, initialize budgets, send orders, or control safe exits.
"""
from decimal import Decimal, InvalidOperation
from .policy import SafetyError
from .simulation_loss_latch import persistent_budget_view
from .simulation_slots import require_capacity


def check_entry(*, risk_path, portfolio, sleeves, slots, controls, sleeve,
                category, required_usd, env):
    # Persist portfolio loss even when another gate (pause/slots) rejects entry.
    view = persistent_budget_view(risk_path, portfolio, sleeves, env=env)
    if (not isinstance(controls, dict) or controls.get('paused') is not False
            or controls.get('stopped') is not False or controls.get('reset_pending') is not False):
        raise SafetyError('Entry controls unavailable or paused')
    from .capital import require_no_pending_migration
    require_no_pending_migration(controls)
    allowed = {'US_STOCK': 'US_STOCK', 'US_OPTION': 'OPTION', 'HK': 'HK_STOCK'}
    if allowed.get(sleeve) != category or sleeve not in allowed:
        raise SafetyError('Unsupported sleeve category; futures remain research')
    if view['entry_block']:
        raise SafetyError('Combined simulation loss stop')
    counts = require_capacity(slots, category, env=env)
    try:
        if isinstance(required_usd, bool):raise ValueError('boolean')
        required = Decimal(str(required_usd))
        if not required.is_finite() or required <= 0:raise ValueError('amount')
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SafetyError('Explicit finite entry cost including fees required') from exc
    if required > view['available'][sleeve]:
        raise SafetyError('Independent sleeve cash insufficient')
    return dict(sleeve=sleeve, category=category, required_usd=required,
                available_usd=view['available'][sleeve], counts=counts,
                combined_loss=view['combined_loss'])


def check_portfolio_entry(*, states, books, hk_snapshot, hk_reservations_path, account,
                   hk_account, portfolio, now, risk_path, controls,
                   sleeve, required_usd):
    """Assemble reconciled US ledgers and verified HK evidence under caller locks.

    Call inside reconciled_account, with HK journals protected by the same
    admission lock. This does not replace fresh news/scan/contract/order checks.
    """
    from .us_budget_inputs import budget_inputs as us_inputs
    from .hk_budget_inputs import budget_inputs as hk_inputs
    from .simulation_slots import ledger_slots, occupied_slots
    if (set(states) != {'US_STOCK', 'US_OPTION'} or sleeve not in (*states,'HK')
            or not portfolio or not account or not hk_account or account == hk_account):
        raise SafetyError('Complete distinct simulation sleeve identities required')
    for name, state in states.items():
        if (state.get('capital_mode') != 'independent_sleeves_v1'
                or state.get('sleeve') != name or state.get('portfolio_id') != portfolio
                or state.get('shared_capital') is not False):
            raise SafetyError('Independent execution identity not initialized')
    from .hk_reservations import Reservations
    hk_slots = Reservations.read_slots(hk_reservations_path, portfolio, hk_account)
    for record in hk_slots:
        if (record.get('account') != hk_account or record.get('owner') != 'HK'
                or record.get('category') != 'HK_STOCK'):
            raise SafetyError('HK slot identity mismatch')
    slots = ledger_slots(states) + hk_slots
    occupied_slots(slots, env='SIMULATE')
    observe_us_loss(states=states,books=books,hk_snapshot=hk_snapshot,account=account,
                    hk_account=hk_account,portfolio=portfolio,now=now,risk_path=risk_path)
    sleeves = {name: us_inputs(state, books, account=account,
                 kind='STOCK' if name=='US_STOCK' else 'OPTION', now=now)
               for name,state in states.items()}
    sleeves['HK'] = hk_inputs(hk_snapshot, account=hk_account, portfolio=portfolio, now=now,
                              for_loss_observation=True)
    # Never let one sleeve's pause be overwritten by healthy peer controls.
    effective = dict(controls)
    for flag in ('paused', 'stopped'):
        effective[flag] = (controls.get(flag) is not False
                           or any(s.get(flag) is not False for s in states.values())
                           or (flag == 'paused' and hk_snapshot['entry_block']))
    return check_entry(risk_path=risk_path, portfolio=portfolio, sleeves=sleeves,
                       slots=slots, controls=effective, sleeve=sleeve,
                       category={'US_STOCK':'US_STOCK','US_OPTION':'OPTION','HK':'HK_STOCK'}[sleeve],
                       required_usd=required_usd, env='SIMULATE')


def check_us_entry(**kwargs):
    if kwargs.get('sleeve') not in ('US_STOCK','US_OPTION'):
        raise SafetyError('Explicit US execution sleeve required')
    return check_portfolio_entry(**kwargs)


def admitted_us_session(root, broker, paths, *, account, hk_account, portfolio,
                      archive, risk_path, sleeve, required_usd, sizing_engine=None,
                      refresh_hk=None):
    """Context for locked reconciliation, valuation, controls and admission.

    No orders are sent. Submission still requires its own fresh market/news
    checks and the independent Engine path, which remains disabled.
    """
    from contextlib import contextmanager
    from pathlib import Path
    from datetime import datetime, timezone
    import json
    from .simulation_reconcile import account_session

    @contextmanager
    def guarded():
        if set(paths) != {'US_STOCK', 'US_OPTION'}:
            raise SafetyError('Explicit execution sleeve paths required')
        base=Path(root)/'data/trading'
        # HK maintenance takes the admission lock itself. Run before acquiring
        # US locks; the persisted snapshot is still fully revalidated afterward.
        # A failed refresh cannot silently fall back to an older successful file.
        if refresh_hk is not None:
            refreshed=refresh_hk()
            if not isinstance(refreshed,dict) or refreshed.get('status')!='reconciled':
                raise SafetyError('HK risk refresh did not reconcile')
        with account_session(root,broker,paths,account=account,archive=archive) as (states,evidence,engines):
            if sleeve not in engines:
                raise SafetyError('Unknown execution sleeve')
            decision=dict(checked_at=datetime.now(timezone.utc).isoformat(),account=account,portfolio=portfolio,
                          sleeve=sleeve,query_started_at=evidence['query_started_at'],
                          required_usd=str(required_usd),submission_enabled=False)
            try:
                codes={code for state in states.values() for code,p in state['positions'].items() if p['qty']}
                books={code:broker.book(code) for code in sorted(codes)}
                hk=json.loads((base/'hk_risk_snapshot.json').read_text())
                controls=json.loads((base/'simulation_controls.json').read_text())
                now=datetime.now(timezone.utc)
                if not 0 <= (now-datetime.fromisoformat(evidence['query_started_at'])).total_seconds() <= 30:
                    raise SafetyError('Account evidence expired during entry preparation')
                result=check_us_entry(states=states,books=books,hk_snapshot=hk,
                    hk_reservations_path=base/'hk_reservations.sqlite',account=account,
                    hk_account=hk_account,portfolio=portfolio,now=now,risk_path=risk_path,
                    controls=controls,sleeve=sleeve,required_usd=required_usd)
                from .swing_engine import SwingEngine
                engine=sizing_engine
                if engine is None and sleeve=='US_STOCK' and isinstance(engines[sleeve],SwingEngine):
                    engine=engines[sleeve]
                    from .paper_strategy import current_scan
                    parent=current_scan(root,broker,datetime.now(timezone.utc))
                    scan=json.loads((base/'market/short_scan.json').read_text())
                    if (not parent.get('complete') or not scan.get('complete')
                            or scan.get('date')!=parent.get('date')
                            or scan.get('source_generated_at')!=parent.get('generated_at')
                            or scan.get('version')!=states[sleeve].get('strategy_version')
                            or any(p['qty'] and code not in scan.get('signals',{})
                                   for code,p in states[sleeve]['positions'].items())):
                        raise SafetyError('Complete matching swing scan required')
                    engine.active_scan=scan
            except Exception as exc:
                engines[sleeve].store.event('independent_admission_rejected',
                    dict(decision,error_type=type(exc).__name__,reason=str(exc)))
                raise
            engines[sleeve].store.event('independent_admission_checked',
                dict(decision,result=json.loads(json.dumps(result,default=str))))
            if engine is None:
                yield states,result,engines
            else:
                from .swing_engine import SwingEngine
                if not isinstance(engine,SwingEngine) or sleeve != 'US_STOCK':
                    raise SafetyError('Stock sizing requires the stock swing engine')
                if getattr(engine,'_independent_sizing',None) is not None:
                    raise SafetyError('Nested independent sizing is forbidden')
                engine._independent_sizing=(states['US_STOCK'],dict(result),
                    datetime.fromisoformat(evidence['query_started_at']))
                try:
                    yield states,result,engines
                finally:
                    if hasattr(engine,'_independent_sizing'):del engine._independent_sizing
    return guarded()


def admitted_us_entry(root, broker, paths, **kwargs):
    """Compatibility view; the session also exposes its live scoped engines."""
    from contextlib import contextmanager
    @contextmanager
    def guarded():
        with admitted_us_session(root,broker,paths,**kwargs) as (states,result,_):
            yield states,result
    return guarded()


def observe_us_loss(*, states, books, hk_snapshot, account, hk_account, portfolio, now, risk_path,
                    hk_loss_observation=None):
    """Observe reconciled risk without requiring a candidate or entry permission.

    Caller holds all account locks and supplies fresh reconciled evidence.
    Unknown orders and unavailable valuations still fail closed.
    """
    from .us_budget_inputs import budget_inputs as us_inputs
    from .hk_budget_inputs import budget_inputs as hk_inputs
    if (set(states)!={'US_STOCK','US_OPTION'} or not account or not hk_account
            or account==hk_account or not portfolio):
        raise SafetyError('Complete distinct simulation sleeve identities required')
    for name,state in states.items():
        if (state.get('capital_mode')!='independent_sleeves_v1' or state.get('sleeve')!=name
                or state.get('portfolio_id')!=portfolio or state.get('shared_capital') is not False):
            raise SafetyError('Independent execution identity not initialized')
    # Observe confirmed portfolio loss before entry-only pending-order gates.
    observation={name:us_inputs(state,books,account=account,
        kind='STOCK' if name=='US_STOCK' else 'OPTION',now=now,for_loss_observation=True)
        for name,state in states.items()}
    if hk_loss_observation is not None:
        # This input carries equity only. It can latch losses but must never
        # expose spendable cash or replace the strict HK entry snapshot.
        from datetime import datetime
        from .simulation_loss_latch import retain_loss_stop
        row=hk_loss_observation
        try:
            stamp=datetime.fromisoformat(row['as_of'])
            if (hk_snapshot is not None or row['account']!=hk_account or row['us_account']!=account
                    or row['portfolio_id']!=portfolio or row['env']!='SIMULATE' or row['currency']!='USD'
                    or row['budget_usd']!=500 or row['inventory_reconciled'] is not True
                    or row['loss_observation_only'] is not True or row['orders_enabled'] is not False
                    or not 0<=(now-stamp).total_seconds()<=30 or isinstance(row['equity_usd'],bool)):
                raise ValueError('identity or freshness')
            equity=Decimal(str(row['equity_usd']))
            if not equity.is_finite():raise ValueError('equity')
        except (KeyError,TypeError,ValueError,InvalidOperation) as exc:
            raise SafetyError('Verified fresh HK loss-only observation required') from exc
        loss=max(Decimal(0),Decimal(1500)-equity-sum(Decimal(str(v['equity'])) for v in observation.values()))
        latched=retain_loss_stop(risk_path,portfolio,env='SIMULATE',triggered=loss>=100)
        return dict(combined_loss=loss,loss_latched=latched,entry_block=True,
                    available={k:Decimal(0) for k in ('US_STOCK','US_OPTION','HK')},loss_observation_only=True)
    observation['HK']=hk_inputs(hk_snapshot,account=hk_account,portfolio=portfolio,now=now,
                                for_loss_observation=True)
    return persistent_budget_view(risk_path,portfolio,observation,env='SIMULATE')


def observe_locked_us_loss(root,broker,paths,*,account,hk_account,portfolio,archive,risk_path,
                           hk_loss_provider=None,service_orders=False):
    """Observe loss; optional owned-order cancellation precedes all valuation.

    Never initializes funds or places orders. Call outside existing ledger locks.
    """
    import json
    from pathlib import Path
    from datetime import datetime,timezone
    from .simulation_reconcile import account_session,cancel_expired_locked
    maintenance_error=None
    if service_orders:
        # The cancellation session releases all locks before this new session.
        # Requery below: never reuse pre-cancel holdings for loss valuation.
        try:
            cancel_expired_locked(root,broker,paths,account=account,archive=archive,
                                  honor_controls=True,honor_news=True)
        except Exception as exc:
            # Independently revalidate the whole account below. A failed cancel
            # must not suppress a provable loss latch; no failure is cleared.
            maintenance_error=exc
    with account_session(root,broker,paths,account=account,archive=archive) as (states,evidence,engines):
        try:
            codes={c for s in states.values() for c,p in s['positions'].items() if p['qty']}
            books={c:broker.book(c) for c in sorted(codes)}
            if hk_loss_provider is not None:
                if not callable(hk_loss_provider):raise SafetyError('Explicit HK loss provider required')
                # Provider runs under the same admission lock and must only read
                # HK evidence; it must not acquire this lock a second time.
                hk_loss=hk_loss_provider()
                if not isinstance(hk_loss,dict):raise SafetyError('Missing HK loss observation')
                hk=None
            else:
                hk_loss=None
                hk=json.loads((Path(root)/'data/trading/hk_risk_snapshot.json').read_text())
            now=datetime.now(timezone.utc)
            if not 0<=(now-datetime.fromisoformat(evidence['query_started_at'])).total_seconds()<=30:
                raise SafetyError('Account evidence expired during loss observation')
            result=observe_us_loss(states=states,books=books,hk_snapshot=hk,account=account,
                hk_account=hk_account,portfolio=portfolio,now=now,risk_path=risk_path,
                hk_loss_observation=hk_loss)
        except Exception as exc:
            for engine in engines.values():engine.store.event('independent_loss_observation_failed',dict(reason=str(exc)))
            raise
        for engine in engines.values():
            engine.store.event('independent_loss_observed',json.loads(json.dumps(dict(evidence=evidence,result=result),default=str)))
        if maintenance_error is not None:
            for engine in engines.values():
                engine.store.event('independent_maintenance_failed',dict(reason=str(maintenance_error),loss_observed=True))
            raise maintenance_error
        return result
