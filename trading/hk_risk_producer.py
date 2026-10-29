"""Reconcile and publish non-flat HK exposure. No worker or binding is created.

Caller must hold the shared admission lock and keep cancellation/exit handling
independent of producer failures. Pending fills/partial exits leave the snapshot
missing (fail closed) until their actual costs and proceeds can be resolved.
"""
import json,os,tempfile
from pathlib import Path
from .policy import SafetyError,quote_price
from .hk_policy import amount
from .hk_fx import validate_accounting_binding
from .hk_reconciliation import inventory
from .hk_settlement import settle_buys,settle_closes
from .hk_fees import order_upper_bound


def publish(path,*,stock,accounts,snapshot,submissions,reservations,fee_records,books,fx,now):
    path=Path(path)
    if (stock.get('env')!='SIMULATE' or stock.get('hk_account')!=submissions.account
        or stock.get('account')==submissions.account or not stock.get('account')
        or not stock.get('portfolio_id') or amount(stock.get('initial'))!=500
        or amount(stock.get('loss_limit'))!=100):
        raise SafetyError('Bound shared simulation identity required')
    if reservations.db.execute('SELECT portfolio FROM identity').fetchone()[0]!=stock['portfolio_id']:
        raise SafetyError('HK reservation portfolio mismatch')
    # Once correctly bound, stale success must not survive any subsequent failure.
    path.unlink(missing_ok=True)
    rate=validate_accounting_binding(fx,stock['portfolio_id'],submissions.account,now)
    inv=inventory(submissions,accounts,snapshot,now)
    settle_buys(submissions,reservations,inv,fee_records,fixed_hkd_per_usd=rate,now=now)
    settle_closes(submissions,reservations,inv,fee_records,fixed_hkd_per_usd=rate,now=now)
    if inv['pending_or_unknown']:raise SafetyError('HK open-order exposure remains unresolved')
    committed=reservations.db.execute("SELECT h.key,s.qty FROM holds h JOIN settled_fills s ON s.key=h.key WHERE h.state='COMMITTED'").fetchall()
    expected={};valuations={};source_times=[inv['as_of']]
    for key,qty in committed:
        entry=submissions.db.execute('SELECT request FROM submissions WHERE tag=?',(key,)).fetchone()
        if not entry:raise SafetyError('HK position has no entry request')
        request=json.loads(entry[0]);code=request['code'];q=amount(qty)
        expected[code]=expected.get(code,amount(0))+q
        book=books.get(code,{})
        bid=amount(quote_price(book,'SELL',now,check_spread=False))
        value=q*bid;exit_fee=amount(order_upper_bound(value,q)['total_hkd'])
        valuations[key]=dict(market_value_usd=str(value/rate),exit_fee_reserve_usd=str(exit_fee/rate))
        source_times.extend([book['bid_time'],book['ask_time']])
    if expected!={k:amount(v) for k,v in inv['positions'].items()}:
        raise SafetyError('HK partial exits or unsettled position value remain unresolved')
    totals=reservations.risk_totals(valuations)
    if totals['pending_or_unknown']:raise SafetyError('HK unresolved reservation remains')
    from datetime import datetime
    oldest=min(source_times,key=lambda value:datetime.fromisoformat(value))
    row=dict(as_of=oldest,published_at=now.isoformat(),account=submissions.account,us_account=stock['account'],
        portfolio_id=stock['portfolio_id'],env='SIMULATE',currency='USD',budget_usd=stock['initial'],
        loss_limit_usd=stock['loss_limit'],reconciled=True,**totals,
        entry_block=bool(stock.get('paused') or stock.get('stopped')),
        phase='reconciled_simulation_exposure',fee_note='Broker evidence or documented simulation estimates; not live fee validation')
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w') as f:json.dump(row,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return row


def observe_loss(*,stock,accounts,snapshot,submissions,fee_records,books,fx,now):
    """Read-only cumulative-fill valuation, including known pending orders.

    Does not settle/release reservations or publish an entry-ready snapshot.
    Complete broker history, explicit fees and fresh inventory remain required.
    """
    from .hk_settlement import fee_amount
    from .engine import TERMINAL
    if (stock.get('env')!='SIMULATE' or stock.get('hk_account')!=submissions.account
            or not stock.get('account') or stock['account']==submissions.account
            or not stock.get('portfolio_id') or amount(stock.get('initial'))!=500
            or amount(stock.get('loss_limit'))!=100):
        raise SafetyError('Bound shared simulation identity required')
    rate=validate_accounting_binding(fx,stock['portfolio_id'],submissions.account,now)
    inv=inventory(submissions,accounts,snapshot,now)
    if fee_records is None:
        from .hk_settlement import simulated_order_fee_records
        fee_records=simulated_order_fee_records(inv,include_pending=True)
    cash=amount(500)*rate;pending_fees=amount(0)
    for tag,body in submissions.db.execute('SELECT tag,request FROM submissions'):
        request=json.loads(body);fill=inv['fills'][tag]
        qty=amount(fill['qty']);notional=qty*amount(fill['average_hkd']);fee=amount(0)
        if qty:
            fee=fee_amount(fee_records.get(fill['order_id']),notional,qty)
            cash+=notional*(1 if request['side']=='SELL' else -1)-fee
        if fill['status'] not in TERMINAL:
            upper=amount(order_upper_bound(amount(request['qty'])*amount(request['price']),amount(request['qty']))['total_hkd'])
            pending_fees+=max(amount(0),upper-fee)
    source_times=[inv['as_of']]
    value=exit_fees=amount(0)
    for code,quantity in inv['positions'].items():
        qty=amount(quantity);bid=amount(quote_price(books.get(code,{}),'SELL',now,check_spread=False))
        source_times.extend([books[code]['bid_time'],books[code]['ask_time']])
        notional=qty*bid;value+=notional
        exit_fees+=amount(order_upper_bound(notional,qty)['total_hkd'])
    from datetime import datetime
    oldest=min(source_times,key=datetime.fromisoformat)
    return dict(account=submissions.account,portfolio_id=stock['portfolio_id'],env='SIMULATE',
        as_of=oldest,us_account=stock['account'],currency='USD',budget_usd=500,
        inventory_reconciled=True,equity_usd=str((cash+value-exit_fees-pending_fees)/rate),
        cash_usd=str(cash/rate),pending_fee_reserve_usd=str(pending_fees/rate),
        exit_fee_reserve_usd=str(exit_fees/rate),pending_order_ids=inv['pending_order_ids'],
        fee_records=fee_records,loss_observation_only=True,orders_enabled=False)


def collect_loss(reader,submissions,stock,*,fx,book_provider,history_start,fee_records=None):
    """Collect HK read-only evidence inside caller's shared admission lock.

    Caller services overdue cancellations first. This function acquires no
    locks and never creates bindings, budgets, reservations or orders.
    """
    from .hk_account import validate_paper_cash
    if (not history_start or reader.account!=submissions.account
            or stock.get('hk_account')!=reader.account or not callable(book_provider)):
        raise SafetyError('Bound HK reader and complete history range required')
    accounts,snapshot=reader.snapshot(history_start)
    inv=inventory(submissions,accounts,snapshot,reader.clock())
    validate_paper_cash(accounts,snapshot,inv,account=reader.account,now=reader.clock())
    # Inventory, not candidate membership, determines required valuation quotes.
    books={code:book_provider(code) for code in sorted(inv['positions'])}
    fees=fee_records() if callable(fee_records) else fee_records
    return observe_loss(stock=stock,accounts=accounts,snapshot=snapshot,submissions=submissions,
        fee_records=fees,books=books,fx=fx,now=reader.clock())


def collect_bound_loss(root,reader,stock,*,book_provider,history_start,fee_records=None):
    """Load existing fixed FX and read-only journal for a locked loss observation.

    The caller owns admission locking and a separate HK read-only context.
    Missing configuration is an error, never an instruction to initialize it.
    """
    from .hk_submission import SubmissionJournal
    base=Path(root)/'data/trading'
    fx=json.loads((base/'hk_fx_binding.json').read_text())
    validate_accounting_binding(fx,stock.get('portfolio_id'),reader.account,reader.clock())
    journal=SubmissionJournal.open_existing_readonly(base/'hk_submissions.sqlite',reader.account)
    try:
        return collect_loss(reader,journal,stock,fx=fx,book_provider=book_provider,
                            history_start=history_start,fee_records=fee_records)
    finally:journal.close()
