"""Service known HK orders independently of quotes, news and entry eligibility."""
from datetime import datetime
from .hk_account import _account_and_time
from .hk_reconciliation import inventory
from .shared_capital import admission_lock
from .engine import TERMINAL
from .policy import SafetyError


def service_orders(journal,transport,accounts,snapshot,now):
    if transport.journal is not journal or snapshot.get('account')!=journal.account or snapshot.get('env')!='SIMULATE':
        raise SafetyError('HK service account mismatch')
    _account_and_time(accounts,journal.account,snapshot.get('checked_at'),now)
    query=snapshot.get('orders',{})
    if type(query.get('return_code')) is not int or query['return_code']!=0 or not isinstance(query.get('response'),list):
        raise SafetyError('Successful HK order query required')
    rows=query['response'];actions=[];errors=[]
    claims=journal.db.execute('SELECT tag,claimed_at FROM submissions ORDER BY claimed_at,tag').fetchall()
    for tag,claimed_at in claims:
        try:
            # Recover timed-out submissions by exact broker identity, never resend.
            with admission_lock(transport.root):
                oid=journal.reconcile(tag,account=journal.account,return_code=0,rows=rows,
                    evidence='HK lifecycle orders '+snapshot['checked_at'])
            matching=[r for r in rows if str(r.get('order_id'))==oid]
            if len(matching)!=1 or not matching[0].get('order_status'):
                raise SafetyError('Unique HK broker status required')
            if matching[0]['order_status'] in TERMINAL:
                actions.append(dict(tag=tag,status='terminal',order_id=oid));continue
            if (now-datetime.fromisoformat(claimed_at)).total_seconds()<60:
                actions.append(dict(tag=tag,status='pending_before_deadline',order_id=oid));continue
            if journal.db.execute('SELECT 1 FROM cancels WHERE tag=?',(tag,)).fetchone():
                actions.append(dict(tag=tag,status='awaiting_cancel_confirmation',order_id=oid));continue
            status=transport.cancel(tag)
            actions.append(dict(tag=tag,status=status,order_id=oid))
        except Exception as exc:
            errors.append(dict(tag=tag,error_type=type(exc).__name__,reason=str(exc)))
    # Unknown activity blocks admission, but must not prevent cancellation of
    # our own otherwise verified orders above.
    verified=None
    if 'positions' in snapshot:
        try:verified=inventory(journal,accounts,snapshot,now)
        except Exception as exc:errors.append(dict(stage='inventory',error_type=type(exc).__name__,reason=str(exc)))
    return dict(as_of=snapshot['checked_at'],inventory_pending='positions' not in snapshot,actions=actions,errors=errors,inventory=verified,
                entry_block=bool(errors) or verified is None or verified['pending_or_unknown'],
                orders_enabled=False)
