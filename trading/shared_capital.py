"""A single stock/quote-paper spending pool; never adds the two nominal capitals."""
import fcntl,os,threading
_lock_owners=threading.local()
from contextlib import contextmanager
from pathlib import Path
from .policy import SafetyError,number

@contextmanager
def admission_lock(root,*,reuse=False):
    path=Path(root)/'data/trading/shared_admission.lock'
    key=(os.getpid(),str(path.resolve()))
    held=getattr(_lock_owners,'held',set())
    if reuse:
        if key not in held:raise SafetyError('Caller does not hold admission lock')
        yield
        return
    if key in held:raise SafetyError('Nested admission lock requires explicit reuse')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        held=set(held);held.add(key);_lock_owners.held=held
        try:yield
        finally:
            _lock_owners.held.discard(key)
            fcntl.flock(handle,fcntl.LOCK_UN)

def available_cash(stock,options):
    from .engine import TERMINAL
    if (not stock.get('shared_capital') or not options.get('shared_capital')
        or stock.get('env')!='SIMULATE' or options.get('env')!='QUOTE_PAPER'
        or not stock.get('portfolio_id') or stock['portfolio_id']!=options.get('portfolio_id')
        or stock['initial']!=options.get('initial') or stock['option_budget_usd']!=options.get('cap')):
        raise SafetyError('Shared capital identity or budget mismatch')
    # Unknown, partially filled or cancelling orders keep admission closed until reconciled.
    if any(o.get('status') not in TERMINAL for o in stock.get('orders',{}).values()):return 0.
    positions=[p for p in stock.get('positions',{}).values() if p['qty']>0]
    option=options.get('position')
    exit_fees=len(positions)*3+(number(options['fee']) if option else 0)
    deployed=sum(number(p['cost']) for p in positions)
    if option:deployed+=number(option['entry_price'])*100
    cash=number(stock['cash'])+number(options['cash'])-number(options['initial'])
    # Realized gains do not automatically raise the user's original investment ceiling.
    from .hk_shared_admission import exposure
    hk=exposure(stock)
    if hk['entry_block']:return 0.
    # HK nominal simulator cash is never added. Realized gains do not expand
    # capacity; realized losses reduce it even after positions are closed.
    return max(0,min(cash,number(stock['initial'])-deployed)-exit_fees
               -hk['occupied_usd']+min(0,hk['realized_net_usd']))

def adopt_shared_pool(db,options_db,now):
    """Called with both workers stopped and both store locks held; preserve control/history."""
    from .capital import config_for_state
    stock=db.load();options=options_db.load()
    if stock.get('shared_capital') and options.get('shared_capital'):
        available_cash(stock,options);return stock,options
    if (stock.get('env')!='SIMULATE' or stock.get('purpose')!='strategy_simulation'
        or options.get('env')!='QUOTE_PAPER' or stock.get('budget_currency')!='USD'
        or not stock.get('portfolio_id') or stock['portfolio_id']!=options.get('portfolio_id')
        or stock.get('enabled_stocks') or stock.get('enabled_options')):
        raise SafetyError('Shared-pool migration identity mismatch')
    if (stock['orders'] or any(p['qty'] for p in stock['positions'].values()) or stock.get('queue')
        or options['position'] or options['trades'] or options['cash']!=options['initial']):
        raise SafetyError('Shared-pool migration requires unused flat strategy ledgers')
    old=options['initial'];new=stock['initial']
    options.setdefault('capital_changes',[]).append(dict(time=now.isoformat(),old_initial=old,new_initial=new,amount_usd=new-old,reason='shared_virtual_pool_reference_only'))
    options.update(initial=new,cash=new,cap=new,equity_usd=new,shared_capital=True)
    stock.update(shared_capital=True,option_budget_usd=new)
    config_for_state(stock).validate();available_cash(stock,options)
    options_db.save(options,'shared_pool_enabled',dict(pool_usd=new,additional_funds_usd=0))
    db.save(stock,'shared_pool_enabled',dict(pool_usd=new,additional_funds_usd=0))
    return stock,options
