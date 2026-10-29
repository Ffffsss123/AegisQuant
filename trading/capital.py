"""Explicit USD plan migration; no reset of historical marks or user controls."""
from uuid import uuid4
from .policy import Config,SafetyError

def config_for_state(s):
    if s.get('budget_currency')=='USD':
        return Config(budget_usd=s['initial'],loss_limit_usd=s['loss_limit'],option_cap_usd=s['option_budget_usd'],shared_capital=s.get('shared_capital',False),allocation=1 if s.get('shared_capital') else .45)
    return Config()

def migrate_usd(b,db,options_db,probe,now,budget,loss,options,total=890):
    config=Config(budget_usd=budget,loss_limit_usd=loss,option_cap_usd=options).validate()
    if total!=890 or budget>total:raise SafetyError('USD funding exceeds declared funds')
    s=db.load();o=options_db.load()
    if s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation' or s.get('account')!=probe.get('account'):
        raise SafetyError('Unexpected simulation identity')
    if o.get('env')!='QUOTE_PAPER':raise SafetyError('Unexpected options observer identity')
    if s.get('budget_currency')=='USD' and s['initial']==budget and s['loss_limit']==loss and s.get('option_budget_usd')==options and o['initial']==options and o.get('portfolio_id')==s.get('portfolio_id'):
        return s,o
    if s['orders'] or any(p['qty'] for p in s['positions'].values()) or s.get('queue') or any(s['baseline'].values()):raise SafetyError('Active stock history requires a separate capital migration')
    if o['trades'] or o['position'] or abs(o['cash']-o['initial'])>1e-8:raise SafetyError('Active options history requires separate migration')
    if s.get('enabled_stocks') or s.get('enabled_options'):raise SafetyError('Live flags must stay disabled')
    b.bind(s['account'],'SIMULATE')
    from .engine import TERMINAL
    known={v['id'] for v in probe['orders'].values() if v.get('id')}
    if any(b.positions().values()) or any(r['id'] not in known or r['status'] not in TERMINAL for r in b.orders(s['bound_at'][:10])):
        raise SafetyError('Unknown broker activity')
    if b.settled_cash()<budget:raise SafetyError('Simulation cash insufficient')
    portfolio=s.get('portfolio_id') or uuid4().hex
    for state,amount in ((s,budget),(o,options)):
        old=state['initial'];delta=amount-old
        state.setdefault('capital_changes',[]).append(dict(time=now.isoformat(),old_initial=old,new_initial=amount,amount_usd=delta))
        state.update(initial=amount,cash=state['cash']+delta,budget_currency='USD',portfolio_id=portfolio)
    s.update(loss_limit=loss,option_budget_usd=options,reserve_usd=total-budget,strategy_acceptance=False)
    if o.get('cycle'):options_db.event('scan_before_capital_change',o['cycle'])
    o.update(cap=options,equity_usd=o['cash'],cycle=None,work=None,next_step_at=None)
    # Workers must be stopped and both locks held. A partial commit fails the identity checks on restart.
    options_db.save(o,'usd_capital_change',dict(amount=options,portfolio_id=portfolio))
    db.save(s,'usd_capital_change',dict(budget=budget,loss=loss,options=options,reserve=total-budget))
    return s,o


def _check_staged_migration(store,migration_id,expected,replacement):
    """Apply/recover one prevalidated stage under caller's admission/store locks.

    No funding authorization or multi-ledger commit decision is supplied here.
    The receipt commits atomically with state; a changed plan/state fails closed.
    """
    import hashlib,json
    if not isinstance(migration_id,str) or not migration_id.strip() or migration_id!=migration_id.strip():
        raise SafetyError('Explicit migration identity required')
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
    receipt=dict(migration_id=migration_id,source_sha256=digest(expected),target_sha256=digest(replacement))
    matches=[]
    for row in store.db.execute("SELECT body FROM events WHERE kind='capital_migration_stage'"):
        recorded=json.loads(row[0])
        if recorded.get('migration_id')==migration_id:matches.append(recorded)
    if matches:
        if len(matches)!=1 or matches[0]!=receipt or not store.exists() or digest(store.load())!=receipt['target_sha256']:
            raise SafetyError('Migration receipt/target mismatch; preserve current ledger')
        return receipt,True
    current=store.load() if store.exists() else None
    if digest(current)!=digest(expected):
        raise SafetyError('Migration source changed; preserve current ledger')
    return receipt,False


def apply_staged_migration(store,migration_id,expected,replacement):
    receipt,completed=_check_staged_migration(store,migration_id,expected,replacement)
    if completed:return False
    store.save_if_current(expected,replacement,'capital_migration_stage',receipt)
    return True


def apply_staged_plan(stages,migration_id):
    """Preflight all sources before any stage write; caller holds every lock.

    Each stage is (Store, expected, replacement). The durable plan and execution
    interlock must already exist; this helper neither creates nor releases them.
    Partial commits retain their receipts for retry with the same plan.
    """
    if not stages or len({str(db.path.resolve()) for db,_,_ in stages})!=len(stages):
        raise SafetyError('Distinct migration target ledgers required')
    for db,expected,target in stages:
        _check_staged_migration(db,migration_id,expected,target)
    return [apply_staged_migration(db,migration_id,expected,target) for db,expected,target in stages]


def apply_staged_plan_locked(root,stages,migration_id):
    """Acquire admission then every target lock before preflight or writes.

    A staged helper only: durable plan, runner shutdown and execution interlock
    remain caller responsibilities. Never call from an already-held session.
    """
    from contextlib import ExitStack
    from .shared_capital import admission_lock
    if not stages or len({str(db.path.resolve()) for db,_,_ in stages})!=len(stages):
        raise SafetyError('Distinct migration target ledgers required')
    with admission_lock(root),ExitStack() as stack:
        for db,_,_ in sorted(stages,key=lambda stage:str(stage[0].path.resolve())):
            stack.enter_context(db.lock())
        return apply_staged_plan(stages,migration_id)


def seal_staged_plan(journal,stages,migration_id):
    """Persist immutable recovery inputs only; never changes target ledgers.

    Caller holds admission, journal and target locks. The journal is separate
    from every target and retains full source/target states for crash recovery.
    """
    import json
    paths=[str(db.path.resolve()) for db,_,_ in stages]
    if (not paths or len(set(paths))!=len(paths) or str(journal.path.resolve()) in paths
            or not isinstance(migration_id,str) or not migration_id.strip()
            or migration_id!=migration_id.strip()):
        raise SafetyError('Invalid durable migration plan identity')
    plan=dict(version=1,migration_id=migration_id,execution_enabled=False,
        stages=[dict(path=str(db.path.resolve()),expected=expected,target=target) for db,expected,target in stages])
    canonical=lambda value:json.dumps(value,sort_keys=True,allow_nan=False)
    if journal.exists():
        if canonical(journal.load())!=canonical(plan):raise SafetyError('Durable migration plan changed')
        return False
    journal.save_if_current(None,plan,'migration_plan_sealed',dict(migration_id=migration_id))
    return True


def apply_sealed_plan_locked(root,journal,stages,migration_id):
    """Apply exactly a previously sealed plan; never enables an executor.

    Runner quiescence, simulation policy and fresh account checks remain required
    externally. Journal and targets stay locked through verification and writes.
    """
    from contextlib import ExitStack
    from .shared_capital import admission_lock
    stores=[journal]+[db for db,_,_ in stages]
    if not stages or len({str(db.path.resolve()) for db in stores})!=len(stores):
        raise SafetyError('Distinct journal and migration ledgers required')
    with admission_lock(root),ExitStack() as stack:
        for db in sorted(stores,key=lambda db:str(db.path.resolve())):
            stack.enter_context(db.lock())
        if not journal.exists():raise SafetyError('Sealed migration plan required before writes')
        seal_staged_plan(journal,stages,migration_id)  # Exact immutable match only.
        result=apply_staged_plan(stages,migration_id)
        journal.event('migration_targets_verified',dict(migration_id=migration_id,
            written=result,execution_enabled=False))
        return result


def snapshot_staged_ledger(store,destination):
    """Create a verified new SQLite copy under the caller's admission/store locks.

    Never overwrite a destination or delete failed-copy evidence. This copies
    state and events; it does not authorize any budget or production migration.
    """
    import sqlite3
    from pathlib import Path
    from contextlib import closing
    destination=Path(destination)
    # Exclusive creation also rejects the source path and existing archives.
    with destination.open('xb'):
        pass
    with closing(sqlite3.connect(str(destination))) as target:
        store.db.backup(target)
    # A fresh normal connection prepares SQLite's WAL bookkeeping for read-only
    # consumers after backup; never remove sidecars or use immutable reads.
    with closing(sqlite3.connect(str(destination))) as target:
        if target.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise SafetyError('Staged SQLite snapshot integrity failure')
    with closing(sqlite3.connect(destination.resolve().as_uri()+'?mode=ro',uri=True)) as target:
        row=target.execute('SELECT body FROM state WHERE id=1').fetchone()
        if row!=store.db.execute('SELECT body FROM state WHERE id=1').fetchone():
            raise SafetyError('Staged SQLite snapshot state mismatch')
    return destination


def require_no_pending_migration(controls):
    """Entry-only interlock; never modifies user controls or enables trading."""
    if not isinstance(controls,dict):
        raise SafetyError('Capital migration controls unavailable')
    if 'capital_migration_pending' in controls and controls['capital_migration_pending'] is not False:
        raise SafetyError('Capital migration pending; new entries disabled')


def begin_staged_migration(root,journal,migration_id):
    """Persist an entry interlock for an already sealed plan; never apply it.

    Caller holds admission and journal locks and has verified worker quiescence.
    This does not grant budget authority, clear user controls, or release locks.
    """
    from pathlib import Path
    from .simulation_controls import update_controls
    plan=journal.load() if journal.exists() else None
    if (not isinstance(plan,dict) or plan.get('version')!=1
            or plan.get('migration_id')!=migration_id or not migration_id
            or plan.get('execution_enabled') is not False or not plan.get('stages')):
        raise SafetyError('Sealed migration plan required for entry interlock')
    def patch(current):
        existing=current.get('capital_migration_id')
        if existing is not None and existing!=migration_id:
            raise SafetyError('Another migration owns the entry interlock')
        if ('capital_migration_pending' in current
                and not isinstance(current['capital_migration_pending'],bool)):
            raise SafetyError('Malformed migration interlock; preserve controls')
        return dict(capital_migration_pending=True,capital_migration_id=migration_id)
    return update_controls(Path(root)/'data/trading/simulation_controls.json',patch)


def finish_staged_migration_locked(root,journal,stages,migration_id,verify_accounts):
    """Release only the migration entry interlock after receipts and fresh checks.

    verify_accounts runs inside all locks, must perform fresh account validation
    without acquiring them again, and must return literal True. User pause/stop
    and all other controls are preserved. No executor is enabled here.
    """
    from contextlib import ExitStack
    from pathlib import Path
    from .shared_capital import admission_lock
    from .simulation_controls import update_controls
    if not callable(verify_accounts):raise SafetyError('Fresh migration account verification required')
    stores=[journal]+[db for db,_,_ in stages]
    if not stages or len({str(db.path.resolve()) for db in stores})!=len(stores):
        raise SafetyError('Distinct journal and migration ledgers required')
    with admission_lock(root),ExitStack() as stack:
        for db in sorted(stores,key=lambda db:str(db.path.resolve())):stack.enter_context(db.lock())
        if not journal.exists():raise SafetyError('Sealed migration plan required')
        seal_staged_plan(journal,stages,migration_id)
        def completed():
            for db,expected,target in stages:
                if not _check_staged_migration(db,migration_id,expected,target)[1]:
                    raise SafetyError('Migration target receipt incomplete')
        completed()
        import time
        checked_started=time.monotonic()
        if verify_accounts() is not True:raise SafetyError('Fresh migration account verification failed')
        completed()
        def patch(current):
            if not 0<=time.monotonic()-checked_started<=30:
                raise SafetyError('Migration account verification expired')
            if (current.get('capital_migration_id')!=migration_id
                    or not isinstance(current.get('capital_migration_pending'),bool)):
                raise SafetyError('Migration interlock identity mismatch')
            return dict(capital_migration_pending=False)
        journal.event('migration_release_verified',dict(migration_id=migration_id,execution_enabled=False))
        return update_controls(Path(root)/'data/trading/simulation_controls.json',patch)


def verify_initial_hk_journals(submissions_db, reservations_db, *, account, portfolio):
    """Validate initial migration's existing HK journals under caller locks.

    Connections must already be open; no schema creation, lock acquisition,
    reservation release or history deletion occurs. Nonempty journals need a
    separate migration preserving their accounting, even when net exposure is 0.
    """
    import sqlite3
    try:
        if not account or not portfolio:
            raise SafetyError('Explicit HK migration identities required')
        if submissions_db.execute('SELECT account FROM binding').fetchall()!=[(str(account),)]:
            raise SafetyError('HK migration submission binding mismatch')
        if reservations_db.execute('SELECT portfolio FROM identity').fetchall()!=[(portfolio,)]:
            raise SafetyError('HK migration reservation portfolio mismatch')
        for db,tables in ((submissions_db,('submissions','aborted_admissions')),
                          (reservations_db,('holds','settled_fills','reservation_intents',
                                            'reservation_identity','closed_positions','closing_orders'))):
            for table in tables:
                if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                    raise SafetyError('Initial HK migration requires empty journals: '+table)
        return True
    except sqlite3.Error as exc:
        raise SafetyError('Cannot verify existing HK migration journals') from exc
