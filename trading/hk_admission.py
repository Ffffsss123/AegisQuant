"""HK buy admission for a future bound runner; call under shared_admission.lock.

The runtime must first reconcile the bound accounts and publish HK exposure.
This module never binds accounts, clears pauses, or enlarges the shared budget.
"""
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime
from .policy import SafetyError
from .hk_policy import amount
from .hk_fx import validate_reference
from .hk_fees import order_upper_bound
from .hk_shared_admission import exposure
from .shared_capital import available_cash
from .news import assess


def fresh(value,now):
    try:
        stamp=datetime.fromisoformat(value)
        if stamp.tzinfo is None or now.tzinfo is None or not 0<=(now-stamp).total_seconds()<=30:raise ValueError()
    except (ValueError,TypeError):raise SafetyError('Fresh reconciled valuation required')


def reserve_buy(request,*,stock,options,controls,reconciliation,news,fx,journal,root,now):
    from .capital import require_no_pending_migration
    require_no_pending_migration(controls)
    if (request.get('side')!='BUY' or request.get('env')!='SIMULATE'
        or request.get('account')!=stock.get('hk_account') or not stock.get('hk_account')
        or stock.get('purpose')!='strategy_simulation'
        or amount(stock.get('initial'))!=500 or amount(stock.get('loss_limit'))!=100):
        raise SafetyError('Bound shared HK simulation required')
    # Count durable unresolved HK entries conservatively as occupied slots.
    us_slots=sum(1 for p in stock.get('positions',{}).values() if amount(p['qty'])!=0)
    hk_slots=journal.db.execute("SELECT count(*) FROM holds WHERE state!='RELEASED'").fetchone()[0]
    if us_slots+hk_slots>=2:
        raise SafetyError('Shared stock position limit reached')
    if any(s.get('paused') or s.get('stopped') for s in (stock,options,controls)):
        raise SafetyError('Preserve user and risk pauses')
    if (reconciliation.get('reconciled') is not True
        or reconciliation.get('account')!=stock.get('account')
        or reconciliation.get('hk_account')!=stock['hk_account']
        or reconciliation.get('portfolio_id')!=stock.get('portfolio_id')
        or reconciliation.get('pending_or_unknown') is not False
        or reconciliation.get('option_transport_active') is not False):
        raise SafetyError('Both-market reconciliation and idle option transport required')
    fresh(reconciliation.get('as_of'),now)
    # The HK component is accounted explicitly below. Do not subtract it twice.
    base=dict(stock);base.pop('hk_account',None)
    cash=amount(available_cash(base,options))
    def equity(state,holding):
        if not holding:return amount(state['cash'])
        marks=state.get('marks',[])
        if not marks:raise SafetyError('Position valuation required')
        fresh(marks[-1].get('time'),now)
        return amount(marks[-1].get('equity_usd'))
    us_equity=equity(stock,any(p['qty'] for p in stock['positions'].values()))
    if options.get('position') and options.get('valuation_valid') is not True:
        raise SafetyError('Option valuation invalid')
    option_equity=equity(options,bool(options.get('position')))
    hk=exposure(stock,now,root)
    if hk['entry_block']:raise SafetyError('HK exposure blocks new entry')
    combined=us_equity+option_equity-amount(options['initial'])+amount(hk['total_net_pnl_usd'])
    if amount(stock['initial'])-combined>=amount(stock['loss_limit']):
        raise SafetyError('Combined loss limit reached')
    decision=assess(news,request['code'],now)
    if decision['global_block'] or decision['exit_long'] or decision['multiplier']<=0:
        raise SafetyError('HK news admission blocked')
    if fx.get('kind')=='fixed_simulation_accounting':
        from .hk_fx import validate_accounting_binding
        rate=validate_accounting_binding(fx,stock['portfolio_id'],stock['hk_account'],now)
    else:
        rate=validate_reference(fx,now)
    notional=amount(request['notional_hkd'])
    if notional!=amount(request['qty'])*amount(request['price']):raise SafetyError('HK request notional mismatch')
    # Non-exempt whole-share rounding bound for both sides; fills reconcile later.
    fee=amount(order_upper_bound(notional,request['qty'])['total_hkd'])
    cost=(notional+2*fee)/rate
    capacity=max(amount(0),cash+min(amount(0),amount(hk['realized_net_usd'])))
    remaining=max(amount(0),capacity-amount(hk['occupied_usd']))
    if cost>remaining*amount(decision['multiplier']):raise SafetyError('Insufficient shared HK entry budget')
    # Virtual HK cash can constrain spending but never adds USD buying power.
    if amount(reconciliation.get('hk_cash_cap_hkd'))<notional+2*fee:
        raise SafetyError('Insufficient verified HK virtual cash')
    path=Path(root)/'data/trading/hk_risk_snapshot.json'
    row=json.loads(path.read_text())
    if journal.db.execute('SELECT portfolio FROM identity').fetchone()[0]!=stock['portfolio_id']:
        raise SafetyError('Reservation journal identity mismatch')
    # Snapshot must reflect every durable hold, including an interrupted attempt.
    if journal.committed_and_held()>amount(row['occupied_usd']):
        raise SafetyError('HK risk snapshot omits durable reservations')
    if journal.db.execute('SELECT 1 FROM holds WHERE key=?',(request['tag'],)).fetchone():
        raise SafetyError('HK reservation already exists; reconcile before retry')
    # Invalidate first: a crash between SQLite and file publication must not
    # leave other markets reading an old snapshot without the new hold.
    path.unlink()
    journal.reserve(request['tag'],cost,capacity,identity=request)
    # Publish the reservation before releasing the cross-market lock. If writing
    # fails, remove the old snapshot so bound US/options fail closed immediately.
    row['occupied_usd']=str(amount(row['occupied_usd'])+cost)
    row.update(pending_or_unknown=True,entry_block=True)
    tmp=None
    try:
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent))
        with os.fdopen(fd,'w') as f:
            json.dump(row,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if tmp and os.path.exists(tmp):os.unlink(tmp)
    return True
