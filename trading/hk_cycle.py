"""One HK maintenance cycle: queries/cancellation precede valuation work.

No candidate submission is performed here. A result named reconciled means
accounting succeeded, never interface or strategy acceptance.
"""
from pathlib import Path
from .hk_lifecycle import service_orders
from .hk_reconciliation import inventory
from .hk_risk_producer import publish
from .shared_capital import admission_lock
from .policy import SafetyError


def run_once(reader,transport,reservations,stock,*,fx,fee_records,books,history_start=None,observe_pending_loss=False):
    path=Path(transport.root)/'data/trading/hk_risk_snapshot.json'
    bound=stock.get('hk_account')==reader.account and stock.get('env')=='SIMULATE'
    report=dict(env='SIMULATE',account_suffix=reader.account[-4:],orders_enabled=False,
                interface_acceptance=False,strategy_acceptance=False,actions=[],errors=[])
    def invalidate():
        if bound:
            with admission_lock(transport.root):path.unlink(missing_ok=True)
    try:
        accounts,snapshot=reader.orders_snapshot(history_start)
        service=service_orders(transport.journal,transport,accounts,snapshot,reader.clock())
        report.update(actions=service['actions'],errors=service['errors'])
        # This runs only after all verified overdue cancellations were serviced.
        snapshot=reader.complete_snapshot(accounts,snapshot)
        inv=inventory(transport.journal,accounts,snapshot,reader.clock())
        report.update(as_of=inv['as_of'],inventory=inv)
        from .hk_account import validate_paper_cash
        cash=validate_paper_cash(accounts,snapshot,inv,account=reader.account,now=reader.clock())
        report['virtual_cash']=cash
        if observe_pending_loss and bound and inv['pending_or_unknown']:
            invalidate()
            # All due cancellations have already run. This optional read-only
            # observation never makes pending orders eligible for settlement.
            from .hk_risk_producer import observe_loss
            fees=fee_records() if callable(fee_records) else fee_records
            quotes=books() if callable(books) else books
            with admission_lock(transport.root):
                report['loss_observation']=observe_loss(stock=stock,accounts=accounts,
                    snapshot=snapshot,submissions=transport.journal,fee_records=fees,
                    books=quotes,fx=fx,now=reader.clock())
        if report['errors'] or inv['pending_or_unknown']:
            invalidate();report['status']='waiting_orders';return report
        if not bound:
            report['status']='awaiting_shared_binding';return report
        from .hk_aborted import recover
        report['recovered_unsubmitted']=recover(transport.journal,reservations,stock,transport.root)
        # Fetch fees/books after servicing orders. Providers must preserve source
        # timestamps; the producer rechecks them after any potentially slow read.
        if fee_records is None:
            from .hk_settlement import simulated_order_fee_records
            fees=simulated_order_fee_records(inv)
        else:
            fees=fee_records() if callable(fee_records) else fee_records
        quotes=books() if callable(books) else books
        with admission_lock(transport.root):
            row=publish(path,stock=stock,accounts=accounts,snapshot=snapshot,
                submissions=transport.journal,reservations=reservations,fee_records=fees,
                books=quotes,fx=fx,now=reader.clock())
        report.update(status='reconciled',risk=row)
    except Exception as exc:
        invalidate();report['status']='needs_attention'
        report['errors'].append(dict(error_type=type(exc).__name__,reason=str(exc)))
    return report
