"""Validate sealed pre-reset broker history without importing old P&L.

Caller verifies archive integrity and fresh complete broker responses under the
admission lock. This pure helper neither initializes funds nor permits orders.
"""
from math import isfinite
from .engine import TERMINAL
from .policy import SafetyError
from .simulation_ownership import reconcile_orders


def reconcile_with_history(ledgers, account, broker_orders, archive):
    if (not isinstance(archive, dict) or archive.get('env') != 'SIMULATE'
            or archive.get('account') != account or archive.get('sealed') is not True
            or not isinstance(archive.get('orders'), list)
            or not isinstance(broker_orders, list)):
        raise SafetyError('Explicit sealed simulation history required')
    def validated(row):
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise SafetyError('Invalid historical order identity')
        if row.get('status') not in TERMINAL:
            raise SafetyError('Historical order is not terminal')
        if any(not isinstance(row.get(k), str) or not row[k] for k in ('code','side')):
            raise SafetyError('Invalid historical instrument')
        for key in ('qty','filled','average'):
            v=row.get(key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) or v<0:
                raise SafetyError('Invalid historical quantity or price')
        if row['qty']<=0 or row['filled']>row['qty'] or (row['filled']>0 and row['average']<=0):
            raise SafetyError('Invalid historical fill')
        return tuple(row[k] for k in ('id','code','side','status','qty','filled','average'))
    expected={}
    for row in archive['orders']:
        signature=validated(row)
        if row['id'] in expected:raise SafetyError('Duplicate historical order')
        expected[row['id']]=signature
    current_ids={str(o.get('id')) for s in ledgers.values() for o in s.get('orders',{}).values()}
    if current_ids & set(expected):raise SafetyError('Historical order reused in current ledger')
    remaining=[];seen=set()
    for row in broker_orders:
        if not isinstance(row,dict):raise SafetyError('Invalid broker row')
        oid=row.get('id')
        if not isinstance(oid,str) or not oid or oid in seen:raise SafetyError('Invalid or duplicate broker identity')
        seen.add(oid)
        if oid in expected:
            if validated(row)!=expected[oid]:raise SafetyError('Historical broker order changed')
        else:remaining.append(row)
    if not set(expected)<=seen:raise SafetyError('Incomplete historical broker query')
    return reconcile_orders(ledgers,account,remaining)


def load_reset_history(directory, *, account, request_id):
    """Read verified final snapshots; never trust a caller's sealed JSON flag."""
    import hashlib
    import json
    import sqlite3
    from pathlib import Path
    root = Path(directory).resolve()
    try:
        manifest = json.loads((root / 'manifest.json').read_text())
        if not request_id or manifest.get('request_id') != request_id:
            raise SafetyError('Reset archive request mismatch')
        records = manifest['databases']
        expected = {r['file']: r for r in records}
        if len(expected) != len(records):
            raise SafetyError('Duplicate archive database identity')
        orders, starts = [], []
        for name in ('paper.sqlite', 'probe.sqlite'):
            path = root / name
            if path.is_symlink() or path.resolve().parent != root:
                raise SafetyError('Archive database escapes directory')
            record = expected[name]
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1048576), b''):
                    digest.update(block)
            if path.stat().st_size != record['size'] or digest.hexdigest() != record['sha256']:
                raise SafetyError('Archive database integrity mismatch')
            db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            finally:
                db.close()
            if state.get('env') != 'SIMULATE' or state.get('account') != account:
                raise SafetyError('Archived account mismatch')
            if any(state.get('baseline', {}).values()) or any(p['qty'] for p in state['positions'].values()):
                raise SafetyError('Archive inventory is not flat')
            starts.append(state['bound_at'][:10])
            for order in state['orders'].values():
                if order.get('fee_final') is not True or order.get('status') not in TERMINAL:
                    raise SafetyError('Unsettled archived order')
                qty = order['booked_qty']
                orders.append(dict(id=order['id'], code=order['code'], side=order['side'], status=order['status'],
                    qty=order['qty'], filled=qty,
                    average=order['booked_notional']/(qty*order['multiplier']) if qty else 0))
        result = dict(env='SIMULATE', account=account, sealed=True, orders=orders, history_start=min(starts))
        # Validate all reconstructed rows and duplicate IDs before returning.
        reconcile_with_history({'empty': dict(env='SIMULATE', account=account, baseline={}, positions={}, orders={})}, account, orders, result)
        return result
    except (OSError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, sqlite3.Error) as exc:
        raise SafetyError('Cannot verify reset archive') from exc


def load_empty_hk_history(directory, *, account, history_start, now):
    """Validate an empty HK evidence window; return an overlapping query start.

    This is not fresh account evidence or permission to trade. Nonempty history
    needs explicit order ownership and is deliberately unsupported here.
    """
    import json,hashlib
    from pathlib import Path
    from datetime import date,datetime
    from .hk_policy import HK
    try:
        base=Path(directory);manifest=json.loads((base/'manifest.json').read_text())
        raw=(base/'raw.jsonl').read_bytes();rows=[json.loads(line) for line in raw.splitlines()]
        if (manifest.get('account')!=account or manifest.get('env')!='SIMULATE'
                or manifest.get('query_start')!=history_start
                or hashlib.sha256(raw).hexdigest()!=manifest.get('raw_sha256')):
            raise ValueError('History identity or digest mismatch')
        if [r['query'] for r in rows]!=['accounts','history_orders','orders','funds','positions']:
            raise ValueError('Incomplete evidence sequence')
        previous=None
        for row in rows:
            begin=datetime.fromisoformat(row['started_at']);end=datetime.fromisoformat(row['completed_at'])
            if (row['account']!=account or row['env']!='SIMULATE' or row['return_code']!=0
                    or begin.tzinfo is None or end.tzinfo is None or not begin<=end<=now
                    or (previous is not None and begin<previous)):
                raise ValueError('Invalid evidence identity or time')
            previous=end
        matched=[r for r in rows[0]['response'] if str(r.get('acc_id'))==account]
        if len(matched)!=1 or matched[0].get('trd_env')!='SIMULATE' or 'HK' not in matched[0].get('trdmarket_auth',[]):
            raise ValueError('HK simulator not identified')
        request=rows[1]['request'];start=date.fromisoformat(history_start);end=date.fromisoformat(request['end'])
        if (request['start']!=history_start or str(request['acc_id'])!=account
                or request['trd_env']!='SIMULATE' or not 0<=(end-start).days<=89
                or end!=datetime.fromisoformat(rows[1]['completed_at']).astimezone(HK).date()
                or manifest.get('query_end')!=end.isoformat()
                or manifest.get('observed_at')!=rows[-1]['completed_at']):
            raise ValueError('Unverified history range')
        for row in rows[2:]:
            if str(row['request'].get('acc_id'))!=account or row['request'].get('trd_env')!='SIMULATE':
                raise ValueError('Wrong snapshot request account')
        if any(rows[i]['response']!=[] for i in (1,2,4)):
            raise ValueError('Nonempty history requires explicit ownership')
        # The last date was observed intraday: querying from the following date
        # would hide orders created later on the evidence date.
        return dict(account=account,env='SIMULATE',history_start=history_start,
                    next_query_start=end.isoformat(),raw_sha256=manifest['raw_sha256'],
                    observed_at=manifest['observed_at'],orders=[],execution_enabled=False)
    except (OSError,KeyError,ValueError,TypeError) as exc:
        raise SafetyError('Invalid empty HK history evidence') from exc


def bound_hk_history_start(stock,*,account,now):
    """Resolve the durable HK history origin and pinned empty-history overlap."""
    if stock.get('env')!='SIMULATE' or stock.get('hk_account')!=account:
        raise SafetyError('Bound HK history identity mismatch')
    start=stock.get('hk_history_start')
    if not start:raise SafetyError('Verified complete HK history start required')
    from datetime import date
    try:date.fromisoformat(start)
    except (TypeError,ValueError) as exc:raise SafetyError('Invalid HK history start') from exc
    checkpoint=stock.get('hk_empty_history')
    if checkpoint is None:return start
    if not isinstance(checkpoint,dict) or not checkpoint.get('directory') or not checkpoint.get('raw_sha256'):
        raise SafetyError('Explicit pinned HK history evidence required')
    evidence=load_empty_hk_history(checkpoint['directory'],account=account,history_start=start,now=now)
    if evidence['raw_sha256']!=checkpoint['raw_sha256']:
        raise SafetyError('Pinned HK history digest mismatch')
    return evidence['next_query_start']
