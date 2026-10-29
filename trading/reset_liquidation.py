"""User-authorized reset exits; retain the ledger until broker-confirmed flat."""
from .engine import TERMINAL
from .policy import SafetyError,quote_price
from .rolling_scan import atomic
from .desktop_server import read_json

def step(engine,state,now,root):
    base=root/'data/trading';request=read_json(base/'reset_request.json')
    def status(value,reason=None):
        atomic(base/'reset_status.json',dict(status=value,reason=reason,updated_at=now.isoformat(),request_id=request.get('id'),reset_complete=False))
    if request.get('liquidate') is not True or request.get('env')!='SIMULATE' or state.get('env')!='SIMULATE' or request.get('account')!=state.get('account') or request.get('bound_at')!=state.get('bound_at'):
        raise SafetyError('Reset liquidation identity mismatch')
    controls=engine.controls()
    if controls.get('stopped') or state.get('stopped'):
        status('blocked','Stop control preserved');return
    engine.reconcile(state,now)
    engine.manage_orders(state,now,reconciled=True)
    if engine.blocked_orders(state):status('reconciling_orders');return
    # Unknown activity or changed inventory must never be masked by a reset.
    rows=engine.b.orders(request['history_start'])
    known=set(request['known_order_ids'])|{o.get('id') for o in state['orders'].values() if o.get('id')}
    if any(o['id'] not in known or o['status'] not in TERMINAL for o in rows):
        raise SafetyError('Unknown or pending broker activity prevents reset')
    owned={c:p['qty'] for c,p in state['positions'].items() if p['qty']}
    actual={c:q for c,q in engine.b.positions().items() if q}
    if actual!=owned or any(state['baseline'].values()) or any(c not in request['positions'] or q>request['positions'][c] for c,q in owned.items()):
        raise SafetyError('Reset inventory differs from authorized owned positions')
    if not owned:status('flat_ready_for_archive');return
    if not engine.b.execution_session(now,strict=False):status('waiting_regular_session');return
    code=sorted(owned)[0]
    prior=[o for o in state['orders'].values() if o.get('execution_purpose')=='user_reset' and o['code']==code]
    if prior:
        status('needs_attention','Reset exit did not fully close; no automatic reprice or duplicate');return
    if state['positions'][code]['kind']!='STOCK':raise SafetyError('Reset exit requires separately verified option contract')
    try:
        book=engine.b.book(code);price=quote_price(book,'SELL',engine.clock(now),False)
    except SafetyError as exc:
        status('waiting_quote',str(exc))
        raise
    engine.submit(state,code,'SELL',owned[code],price,now,execution_purpose='user_reset',exit_reason='user_reset')
    status('exit_submitted')
