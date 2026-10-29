"""Build explicit local ownership for one simulated account; no broker mutations."""
from math import isfinite
from .policy import SafetyError

def ownership(ledgers, account):
    if not isinstance(account, str) or not account or not ledgers:
        raise SafetyError('Explicit simulation account and ledgers required')
    orders, positions, owners = {}, {}, {}
    for owner, state in ledgers.items():
        if not isinstance(state, dict) or any(not isinstance(state.get(k), dict) for k in ('baseline', 'positions', 'orders')):
            raise SafetyError('Incomplete ownership ledger')
        if not isinstance(owner, str) or not owner or state.get('env') != 'SIMULATE' or str(state.get('account')) != account:
            raise SafetyError('Simulation ownership account mismatch')
        # Baseline inventory is external; assigning it to a sleeve would mask manual activity.
        if any(state.get('baseline', {}).values()):
            raise SafetyError('External baseline requires separate reconciliation')
        for order in state.get('orders', {}).values():
            oid = order.get('id')
            if not oid:
                raise SafetyError('Unresolved order identity')
            oid = str(oid)
            if oid in orders:
                raise SafetyError('Duplicate broker order ownership')
            orders[oid] = owner
        for code, position in state.get('positions', {}).items():
            qty = position.get('qty')
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not isfinite(qty) or qty < 0:
                raise SafetyError('Invalid owned inventory')
            if not qty:
                continue
            if code in owners:
                raise SafetyError('Ambiguous instrument ownership')
            owners[code] = owner
            positions[code] = qty
    return dict(order_owners=orders, position_owners=owners, positions=positions)


def reconcile_positions(ledgers, account, broker_positions):
    """Compare a complete normalized broker inventory with the local union.

    Caller must verify query success, freshness and account identity and hold
    the admission lock across ledger reads. This does not grant admission.
    """
    local = ownership(ledgers, account)
    if not isinstance(broker_positions, list):
        raise SafetyError('Complete broker inventory required')
    actual, seen = {}, set()
    for row in broker_positions:
        if not isinstance(row, dict):
            raise SafetyError('Invalid broker inventory row')
        code, qty = row.get('code'), row.get('qty')
        if not isinstance(code, str) or not code or code != code.strip() or code in seen:
            raise SafetyError('Invalid or duplicate broker instrument')
        seen.add(code)
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not isfinite(qty) or qty < 0:
            raise SafetyError('Invalid broker inventory quantity')
        if qty:
            actual[code] = qty
    if actual != local['positions']:
        raise SafetyError('Broker inventory differs from owned ledger union')
    return local


def reconcile_orders(ledgers, account, broker_orders):
    """Verify already-booked normalized orders; never infer or book fills.

    A newer broker fill fails this check until its owning ledger reconciles it.
    Caller supplies complete, fresh, account-verified current/history results
    merged by ID under the admission lock. Unknown history also fails closed.
    """
    local = ownership(ledgers, account)
    if not isinstance(broker_orders, list):
        raise SafetyError('Complete broker orders required')
    expected = {str(o['id']): o for s in ledgers.values() for o in s['orders'].values()}
    seen = set()
    for row in broker_orders:
        if not isinstance(row, dict):
            raise SafetyError('Invalid broker order row')
        oid = str(row.get('id', ''))
        if oid not in expected or oid in seen:
            raise SafetyError('Unknown or duplicate broker order')
        seen.add(oid)
        own = expected[oid]
        for key in ('code', 'side', 'status'):
            if not isinstance(row.get(key), str) or not row[key] or row[key] != own.get(key):
                raise SafetyError('Broker order identity or status mismatch')
        for remote_key, local_key in (('qty', 'qty'), ('filled', 'booked_qty')):
            a, b = row.get(remote_key), own.get(local_key)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v < 0 for v in (a, b)) or a != b:
                raise SafetyError('Broker order quantity differs from booked ledger')
        if row['qty'] <= 0 or row['filled'] > row['qty']:
            raise SafetyError('Invalid cumulative broker fill')
    if seen != set(expected):
        raise SafetyError('Missing owned broker orders')
    return local


def verify_owner_flat(ledgers, account, owner, broker_orders, broker_positions):
    """Prove one sleeve flat after full union reconciliation, retaining peers.

    Caller verifies broker session, freshness and completed transport phases.
    This helper alone is neither transport acceptance nor permission to trade.
    """
    from .engine import TERMINAL
    if owner not in ledgers:
        raise SafetyError('Missing target sleeve')
    reconcile_orders(ledgers, account, broker_orders)
    union = reconcile_positions(ledgers, account, broker_positions)
    if owner in union['position_owners'].values():
        raise SafetyError('Target sleeve still owns inventory')
    if any(o.get('status') not in TERMINAL or o.get('fee_final') is not True
           for o in ledgers[owner]['orders'].values()):
        raise SafetyError('Target sleeve orders or fees unresolved')
    return union
