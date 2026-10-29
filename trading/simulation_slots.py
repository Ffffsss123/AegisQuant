"""Pure slot validation for future independent simulation sleeves; no order access."""
from .policy import SafetyError

CATEGORIES = ('US_STOCK', 'HK_STOCK', 'OPTION', 'FUTURE')

def occupied_slots(records, *, env):
    """Count normalized holdings and unresolved entry claims once per instrument.

    Callers must supply a complete reconciled inventory under the admission lock.
    This function neither validates that inventory nor enables a trading product.
    """
    if env != 'SIMULATE':
        raise SafetyError('Category slots are restricted to SIMULATE')
    occupied = {category: set() for category in CATEGORIES}
    identities = {}
    for record in records:
        if not isinstance(record, dict):
            raise SafetyError('Unverified slot record')
        category = record.get('category')
        code = record.get('code')
        account = record.get('account')
        owner = record.get('owner')
        if category not in occupied or not all(isinstance(v, str) and v.strip() for v in (code, account, owner)):
            raise SafetyError('Unverified slot identity')
        if any(v != v.strip() for v in (code, account, owner)):
            raise SafetyError('Noncanonical slot identity')
        if category in ('US_STOCK', 'HK_STOCK') and not code.startswith(category[:2] + '.'):
            raise SafetyError('Stock slot market mismatch')
        if record.get('state') not in ('HELD', 'ENTRY_PENDING', 'ENTRY_UNKNOWN'):
            raise SafetyError('Unverified slot state')
        identity = (account, code)
        claim = (owner, category)
        if identity in identities and identities[identity] != claim:
            raise SafetyError('Conflicting instrument ownership')
        identities[identity] = claim
        occupied[category].add(identity)
    return {category: len(values) for category, values in occupied.items()}

def require_capacity(records, category, *, env):
    counts = occupied_slots(records, env=env)
    if category not in counts or counts[category] >= 2:
        raise SafetyError('Simulation category slot limit')
    return counts


def ledger_slots(ledgers):
    """Map reconciled Engine ledgers to slot claims; HK journals remain separate.

    Unsent and unknown BUY intents still occupy slots. This does not verify
    broker reconciliation or include external reservation journals.
    """
    from math import isfinite
    from .engine import TERMINAL
    records = []
    def category(code, kind):
        if kind == 'OPTION': return 'OPTION'
        if kind == 'STOCK' and isinstance(code, str):
            if code.startswith('US.'): return 'US_STOCK'
            if code.startswith('HK.'): return 'HK_STOCK'
        raise SafetyError('Unsupported ledger instrument category')
    for owner, ledger in ledgers.items():
        if ledger.get('env') != 'SIMULATE' or any(not isinstance(ledger.get(k), dict) for k in ('positions', 'orders')):
            raise SafetyError('Incomplete simulation slot ledger')
        account = str(ledger.get('account', ''))
        if account in ('', 'None'): raise SafetyError('Missing slot account')
        for code, p in ledger['positions'].items():
            qty = p.get('qty')
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not isfinite(qty) or qty < 0:
                raise SafetyError('Invalid slot inventory')
            if qty:
                records.append(dict(category=category(code,p.get('kind')),code=code,account=account,owner=owner,state='HELD'))
        for order in ledger['orders'].values():
            if order.get('status') in TERMINAL: continue
            if order.get('side') not in ('BUY','SELL'): raise SafetyError('Unknown slot order direction')
            if order['side'] == 'BUY':
                code=order.get('code')
                records.append(dict(category=category(code,order.get('kind')),code=code,account=account,owner=owner,
                                    state='ENTRY_UNKNOWN' if order.get('status') == 'UNKNOWN' else 'ENTRY_PENDING'))
    occupied_slots(records, env='SIMULATE')
    return records
