"""Book each owner's fills from one complete account snapshot under caller locks.

Caller holds admission plus every ledger lock, verifies account and freshness,
and supplies archive-validated historical IDs. No order mutation is performed.
"""
from contextlib import contextmanager
from math import isfinite
from .policy import SafetyError
from .simulation_ownership import ownership, reconcile_orders, reconcile_positions


def recover_order_identities(states, account, orders):
    """Resolve lost acknowledgements from unique durable tags; never resend."""
    from copy import deepcopy
    candidate=deepcopy(states)
    tags=set()
    for state in candidate.values():
        for tag,local in state['orders'].items():
            if not isinstance(tag,str) or not tag or tag in tags:
                raise SafetyError('Ambiguous durable order tag')
            tags.add(tag)
            if local.get('id'):continue
            matches=[row for row in orders if row.get('tag')==tag]
            if len(matches)!=1:raise SafetyError('Unresolved or duplicate broker tag')
            row=matches[0]
            if (not isinstance(row.get('id'),str) or not row['id']
                    or any(row.get(k)!=local.get(k) for k in ('code','side','qty'))):
                raise SafetyError('Recovered broker identity mismatch')
            local['id']=row['id']
    ownership(candidate,account)  # Includes collision with every peer's known ID.
    return candidate


def reconcile_engines(engines, states, account, orders, positions, now):
    if set(engines) != set(states):raise SafetyError('Complete engine/ledger union required')
    recovered = recover_order_identities(states, account, orders)
    states.clear(); states.update(recovered)
    owners={};instruments={}
    for owner,state in states.items():
        for code,p in state['positions'].items():
            if p['qty']:
                if code in instruments and instruments[code]!=owner:raise SafetyError('Ambiguous instrument')
                instruments[code]=owner
        for order in state['orders'].values():
            owners[str(order['id'])]=(owner,order)
            code=order['code']
            if code in instruments and instruments[code]!=owner:raise SafetyError('Ambiguous order instrument')
            instruments[code]=owner
    if not isinstance(orders,list) or not isinstance(positions,dict):raise SafetyError('Complete broker snapshot required')
    seen=set()
    for row in orders:
        if not isinstance(row,dict):raise SafetyError('Invalid broker order row')
        oid=row.get('id')
        if oid not in owners or oid in seen:raise SafetyError('Unknown or duplicate broker order')
        seen.add(oid);_,local=owners[oid]
        if any(row.get(k)!=local.get(k) for k in ('code','side','qty')):
            raise SafetyError('Broker identity changed')
        values=[row.get(k) for k in ('qty','filled','average')]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) or v<0 for v in values):
            raise SafetyError('Invalid broker cumulative fill')
        qty,filled,average=values
        if qty<=0 or not local['booked_qty']<=filled<=qty or (filled>0 and average<=0):
            raise SafetyError('Invalid or decreasing broker fill')
    if seen!=set(owners):raise SafetyError('Missing owned order')
    if any(isinstance(q,bool) or not isinstance(q,(int,float)) or not isfinite(q) or q<0 for q in positions.values()):
        raise SafetyError('Invalid broker inventory quantity')
    if any(q and code not in instruments for code,q in positions.items()):
        raise SafetyError('External inventory')
    class Snapshot:
        def __init__(self, broker, owner):self.broker=broker;self.owner=owner
        def orders(self,*args):return [r for r in orders if owners[r['id']][0]==self.owner]
        def positions(self):return {c:q for c,q in positions.items() if instruments.get(c)==self.owner}
        def __getattr__(self,name):
            if name not in ('fee',):raise SafetyError('Only fee reads allowed during snapshot reconciliation')
            return getattr(self.broker,name)
    for owner,engine in engines.items():
        original=engine.b
        try:
            engine.b=Snapshot(original,owner)
            native=getattr(engine,'_snapshot_reconcile',engine.reconcile)
            native(states[owner],now)
        finally:engine.b=original
    reconcile_orders(states,account,orders)
    return reconcile_positions(states,account,[dict(code=c,qty=q) for c,q in positions.items()])


@contextmanager
def account_session(root, broker, paths, *, account, archive, stock_exit_owner=None, option_exit_owner=None, entry_owner=None, entry_validator=None, reuse_admission=False):
    """Keep admission and ledger locks held for the caller's next risk checks.

    Archive must come from load_reset_history. All paths are existing ledgers;
    absent option ledgers are not initialized here and no budgets are deployed.
    stock_exit_owner or option_exit_owner grants one scoped SELL transport
    for its asset kind; default sessions remain read-only. Production routing
    must separately opt in.
    """
    from contextlib import ExitStack
    from datetime import datetime, timezone
    from .shared_capital import admission_lock
    from .simulation_snapshot import locked_ledgers
    from .simulation_history import reconcile_with_history
    from .engine import Engine
    from .capital import config_for_state
    from .store import Store
    with admission_lock(root,reuse=reuse_admission), locked_ledgers(paths) as states:
        # Validate account/baseline/known ownership before querying, but retain
        # unresolved intents so the complete broker response can recover them.
        preliminary={k:dict(v,orders={tag:o for tag,o in v['orders'].items() if o.get('id')})
                     for k,v in states.items()}
        ownership(preliminary, account)
        if entry_owner is not None and (entry_owner not in ('US_STOCK','US_OPTION') or not callable(entry_validator)):
            raise SafetyError('Explicit independent entry owner and validator required')
        if entry_owner is not None and (stock_exit_owner is not None or option_exit_owner is not None):
            raise SafetyError('Entry and exit capabilities cannot share a session')
        if stock_exit_owner is not None and option_exit_owner is not None:
            raise SafetyError('Only one scoped exit owner per session')
        exit_owner=entry_owner if entry_owner is not None else (stock_exit_owner if stock_exit_owner is not None else option_exit_owner)
        if exit_owner is not None and exit_owner not in states:
            raise SafetyError('Unknown scoped exit owner')
        # query_snapshot binds inside its measured freshness window before every read.
        started = datetime.now(timezone.utc)
        start = min([s['bound_at'][:10] for s in states.values()] + [archive['history_start']])
        # Persist each completed read before starting the next: a positions
        # timeout must not discard the already received order identities.
        import json, os
        from pathlib import Path
        audit_path=Path(root)/'data/trading/joint_account_evidence.jsonl'
        audit_path.parent.mkdir(parents=True,exist_ok=True)
        def audit_value(value):
            # Preserve malformed numeric evidence without writing invalid JSON
            # or altering the original values used by reconciliation guards.
            if isinstance(value,float) and not isfinite(value):
                return {'nonfinite_float':repr(value)}
            if isinstance(value,dict):return {k:audit_value(v) for k,v in value.items()}
            if isinstance(value,(list,tuple)):return [audit_value(v) for v in value]
            return value
        def record_response(stage, payload):
            record=dict(query_started_at=started.isoformat(),received_at=datetime.now(timezone.utc).isoformat(),
                        account=account,env='SIMULATE',history_start=start,stage=stage,**payload)
            with audit_path.open('a') as handle:
                handle.write(json.dumps(audit_value(record),allow_nan=False,default=str)+'\n')
                handle.flush();os.fsync(handle.fileno())
        def query_snapshot():
            nonlocal started
            started=datetime.now(timezone.utc)
            broker.bind(account,'SIMULATE')
            orders = broker.orders(start)
            record_response('orders_received',dict(orders=orders))
            positions = broker.positions()
            record_response('snapshot_received',dict(orders=orders,positions=positions))
            if (datetime.now(timezone.utc)-started).total_seconds()>30:
                raise SafetyError('Account reconciliation snapshot expired')
            historical_ids = {r['id'] for r in archive['orders']}
            historical = [r for r in orders if r['id'] in historical_ids]
            empty = {'archive':dict(env='SIMULATE',account=account,baseline={},positions={},orders={})}
            reconcile_with_history(empty,account,historical,archive)
            current = [r for r in orders if r['id'] not in historical_ids]
            if any(str(o.get('id')) in historical_ids for s in states.values() for o in s['orders'].values()):
                raise SafetyError('Historical order imported into active ledger')
            return current,positions,historical
        current,positions,historical=query_snapshot()
        with ExitStack() as stack:
            engines={}
            for owner,path in paths.items():
                store=Store(path);stack.callback(store.close)
                engine_type=Engine
                if states[owner].get('strategy_version')=='swing_1_5_v1':
                    from .swing_engine import SwingEngine
                    engine_type=SwingEngine
                engines[owner]=engine_type(broker,store,config_for_state(states[owner]))
            # Maintenance must see user controls even when entry admission is
            # paused or unavailable. Read lazily so reconciliation stays usable
            # when controls/news need repair, and never cache a pause decision.
            from .news import NewsGuard
            base=Path(root)/'data/trading'
            def current_controls():
                value=json.loads((base/'simulation_controls.json').read_text())
                if not isinstance(value,dict):raise SafetyError('Invalid simulation controls')
                return value
            for engine in engines.values():
                engine.controls=current_controls
                engine.news=NewsGuard(base/'news/latest.json')
            union=reconcile_engines(engines,states,account,current,positions,datetime.now(timezone.utc))
            evidence = dict(checked_at=datetime.now(timezone.utc).isoformat(),query_started_at=started.isoformat(),
                            historical_orders=len(historical),current_orders=len(current),ownership=union)
            # One owner for all locks and open stores. Engines are scoped to this
            # session, not an authorization to send orders or reuse snapshots.
            class ReadOnlyBroker:
                def __getattr__(self,name):
                    if name in ('place','cancel'):
                        raise SafetyError('Account readiness session cannot mutate broker orders')
                    return getattr(broker,name)
            for engine in engines.values():engine.b=ReadOnlyBroker()
            session_open=True
            def refresh_account():
                if not session_open:raise SafetyError('Account session is closed')
                # A new snapshot invalidates any prior sizing decision, including
                # when the query fails. Call admission again before sizing.
                for engine in engines.values():
                    if hasattr(engine,'_independent_sizing'):del engine._independent_sizing
                evidence.clear()
                prepared.clear()
                current,positions,historical=query_snapshot()
                union=reconcile_engines(engines,states,account,current,positions,datetime.now(timezone.utc))
                evidence.clear()
                evidence.update(checked_at=datetime.now(timezone.utc).isoformat(),query_started_at=started.isoformat(),
                                historical_orders=len(historical),current_orders=len(current),ownership=union)
                return dict(evidence)
            def owned_refresh(owner,local_state):
                from copy import deepcopy
                if not session_open:raise SafetyError('Account session is closed')
                # Never erase uncommitted decisions or silently use a state from
                # another owner. Native strategy routing must explicitly opt in.
                persisted={name:engine.store.load() for name,engine in engines.items()}
                if local_state!=persisted[owner]:
                    raise SafetyError('Owned state changed before joint refresh')
                # Native decisions may already have been saved in this session.
                # Reload every owner before reconciliation, never restore an old
                # in-memory snapshot over a durable stop, queue or order intent.
                states.clear();states.update(persisted)
                refreshed=refresh_account()
                local_state.clear();local_state.update(deepcopy(states[owner]))
                return refreshed
            prepared={}
            consumed=set()
            def before_order_intent(owner,state,order,tag=None):
                if not session_open:raise SafetyError('Account session is closed')
                if state!=engines[owner].store.load():
                    raise SafetyError('Owned state changed before submission intent')
                stamp=evidence.get('query_started_at')
                if not stamp or not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()<=30:
                    raise SafetyError('Joint account evidence expired before submission intent')
                if state.get('env')!='SIMULATE' or str(state.get('account'))!=account:
                    raise SafetyError('Joint submission account mismatch')
                if entry_owner is not None:
                    if (owner!=entry_owner or state.get('capital_mode')!='independent_sleeves_v1'
                            or state.get('sleeve')!=owner or order.get('side')!='BUY'
                            or order.get('kind')!=('STOCK' if owner=='US_STOCK' else 'OPTION')):
                        raise SafetyError('Independent entry capability identity mismatch')
                    if entry_validator(states,engines,evidence,state,order) is not True:
                        raise SafetyError('Independent entry admission rejected')
                    if not tag or tag in state['orders'] or tag in consumed:
                        raise SafetyError('Invalid scoped entry tag')
                    from copy import deepcopy
                    prepared.clear();prepared[tag]=deepcopy(order)
                    return
                kind='OPTION' if owner==option_exit_owner and option_exit_owner is not None else 'STOCK'
                multiplier=100 if kind=='OPTION' else 1
                if order.get('side')!='SELL' or order.get('kind')!=kind:
                    raise SafetyError('Joint submission supports stock exits only' if kind=='STOCK' else 'Joint submission supports option exits only')
                if kind=='OPTION':
                    contract=state.get('probe_contract',{})
                    if (contract.get('code')!=order.get('code') or contract.get('standard') is not True
                            or contract.get('multiplier')!=100 or order.get('qty')!=1
                            or any(not contract.get(k) or contract.get(k)!=order.get(k) for k in ('expiry','underlying','right'))):
                        raise SafetyError('Unverified owned option exit contract')
                position=state['positions'].get(order['code'],{})
                if (evidence['ownership']['position_owners'].get(order['code'])!=owner
                        or position.get('kind')!=kind or position.get('multiplier')!=multiplier
                        or not 0<order['qty']<=position.get('qty',0)):
                    raise SafetyError('Joint exit ownership mismatch')
                if owner==exit_owner:
                    from copy import deepcopy
                    if not tag or tag in state['orders'] or tag in consumed:
                        raise SafetyError('Invalid scoped exit tag')
                    prepared.clear()
                    prepared[tag]=deepcopy(order)

            class ScopedExitBroker(ReadOnlyBroker):
                def place(self,code,side,qty,price,tag):
                    # Consume before any transport call: an uncertain response
                    # must only be recovered by joint reconciliation, never sent twice.
                    expected=prepared.pop(tag,None)
                    if not session_open or tag in consumed or expected is None:
                        raise SafetyError('No active scoped exit intent')
                    consumed.add(tag)
                    state=engines[exit_owner].store.load()
                    if (state['orders'].get(tag)!=expected or expected.get('status')!='INTENT'
                            or expected.get('id') is not None
                            or (code,side,qty,price)!=(expected['code'],expected['side'],expected['qty'],expected['price'])):
                        raise SafetyError('Scoped exit differs from durable intent')
                    stamp=evidence.get('query_started_at')
                    if not stamp or not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()<=30:
                        raise SafetyError('Scoped exit evidence expired')
                    controls=current_controls()
                    if entry_owner is not None:
                        from .capital import require_no_pending_migration
                        require_no_pending_migration(controls)
                        if (state.get('paused') or controls.get('paused') is not False
                                or controls.get('reset_pending') is not False):
                            raise SafetyError('Entry controls changed before transport')
                    if state.get('stopped') or controls.get('stopped'):

                        raise SafetyError('Stop control blocks scoped exit')
                    return broker.place(code,side,qty,price,tag)

            if exit_owner is not None:
                engines[exit_owner].b=ScopedExitBroker()
                if entry_owner is not None:engines[entry_owner]._independent_entry_session=True

            def strategy_reconcile(owner,state):
                owned_refresh(owner,state)
                return state  # Preserve Engine/SwingEngine reconcile's return contract.
            for owner,engine in engines.items():
                engine.refresh_account=refresh_account
                engine.before_order_intent=lambda state,order,tag=None,owner=owner:before_order_intent(owner,state,order,tag)
                engine.refresh_owned_state=lambda state,owner=owner:owned_refresh(owner,state)
                # Snapshot reconciliation must retain native accounting, while
                # strategy calls on this scoped engine use the complete account.
                engine._snapshot_reconcile=engine.reconcile
                engine.reconcile=lambda state,now,owner=owner:strategy_reconcile(owner,state)
            try:
                yield states, evidence, engines
            finally:
                session_open=False
                if entry_owner is not None:engines[entry_owner]._independent_entry_session=False


@contextmanager
def reconciled_account(root, broker, paths, *, account, archive):
    """Compatibility view of the single lock-owning account session."""
    with account_session(root,broker,paths,account=account,archive=archive) as (states,evidence,_):
        yield states,evidence


def reconcile_locked(root, broker, paths, *, account, archive):
    """Read/accounting-only convenience wrapper; no entry authorization."""
    with reconciled_account(root, broker, paths, account=account, archive=archive) as (_, evidence):
        return evidence


def cancel_expired_locked(root,broker,paths,*,account,archive,honor_controls=False,honor_news=False):
    """Cancel only owned SIMULATE orders aged 60s after complete reconciliation.

    Opt-in controls cancel paused BUYs/stopped orders; news can cancel BUYs.
    Already-required safety cancellations never consult news.
    No new order, repricing, cash release or entry permission is provided.
    Production dispatch remains separate from this maintenance entry point.
    """
    from datetime import datetime,timezone
    from .engine import TERMINAL
    actions=[];errors=[]
    with account_session(root,broker,paths,account=account,archive=archive) as (states,evidence,engines):
        targets=[(owner,tag) for owner,state in states.items() for tag in state['orders']]
        # Service already-expired orders before potentially slow news checks on
        # fresh entries, regardless of ledger insertion order.
        priority_time=datetime.now(timezone.utc)
        def expiry_priority(target):
            owner,tag=target;order=states[owner]['orders'][tag]
            if order['status'] in TERMINAL:return 2
            return 0 if (priority_time-datetime.fromisoformat(order['created'])).total_seconds()>=60 else 1
        targets.sort(key=expiry_priority)
        for owner,tag in targets:
            # Refresh replaces state objects; never keep a stale order reference.
            order=states[owner]['orders'][tag]
            if order['status'] in TERMINAL:continue
            now=datetime.now(timezone.utc)
            expired=(now-datetime.fromisoformat(order['created'])).total_seconds()>=60
            must_cancel=expired
            reason='expired_60s' if expired else None
            if not must_cancel and honor_controls:
                state=states[owner]
                # A failed controls read must not prevent later expired orders.
                try:controls=engines[owner].controls()
                except Exception as exc:
                    controls={};errors.append(exc)
                stopped=state.get('stopped') or controls.get('stopped')
                paused=state.get('paused') or controls.get('paused') or controls.get('reset_pending')
                must_cancel=stopped or (paused and order['side']=='BUY')
                if must_cancel:reason='stopped' if stopped else 'entry_paused'
            if not must_cancel and honor_news and order['side']=='BUY':
                try:
                    must_cancel=engines[owner].news_check(states[owner],order.get('underlying') or order['code'],now)==0
                    if must_cancel:reason='news_blocked'
                except Exception as exc:
                    engines[owner].alert(states[owner],'Maintenance news check failed: '+str(exc))
                    errors.append(exc)
                    continue
            if not must_cancel:continue
            engines[owner].refresh_account()
            state=states[owner];order=state['orders'][tag]
            if order['status'] in TERMINAL:continue
            if not order.get('id'):raise SafetyError('Cannot cancel unresolved order identity')
            order['cancel_requested']=True
            decision=dict(tag=tag,source='joint_order_maintenance',reason=reason,
                          checked_at=now.isoformat())
            if reason=='news_blocked':
                decision['news_decision']=state.get('news_status',{}).get(order.get('underlying') or order['code'])
            engines[owner].store.save(state,'cancel_intent',decision)
            error=None
            try:broker.cancel(order['id'])
            except Exception as exc:
                error=exc
                engines[owner].store.event('cancel_uncertain',dict(tag=tag,reason=str(exc)))
            # Query even after a timeout. Never synthesize a terminal status.
            engines[owner].refresh_account()
            current=states[owner]['orders'][tag]
            actions.append(dict(owner=owner,tag=tag,id=current['id'],status=current['status'],reason=reason))
            if current['kind']=='OPTION' and current['side']=='SELL' and current['booked_qty']<current['qty']:
                engines[owner].alert(states[owner],'Option exit unfilled: manual action required '+current['code'])
            if error is not None and current['status'] not in TERMINAL:
                engines[owner].alert(states[owner],'Cancel not confirmed '+tag+': '+str(error))
                errors.append(error)
        if errors:raise errors[0]
    return actions
