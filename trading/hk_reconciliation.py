"""Reconcile HK actual filled quantities against all durable submission claims.

No cash release, fee inference or acceptance claim occurs here. A matched open
order still blocks new entries; missing history and unknown activity fail closed.
"""
import json
from .hk_account import _account_and_time
from .hk_policy import amount
from .policy import SafetyError
from .engine import TERMINAL


def inventory(journal,accounts,snapshot,now):
    if snapshot.get('account')!=journal.account or snapshot.get('env')!='SIMULATE':
        raise SafetyError('HK snapshot account mismatch')
    _account_and_time(accounts,journal.account,snapshot.get('checked_at'),now)
    responses={}
    for key in ('orders','positions'):
        row=snapshot.get(key,{})
        if type(row.get('return_code')) is not int or row['return_code']!=0 or not isinstance(row.get('response'),list):
            raise SafetyError('Successful HK inventory queries required')
        responses[key]=row['response']
    requests={tag:(json.loads(body),oid) for tag,body,oid in journal.db.execute('SELECT tag,request,order_id FROM submissions')}
    rows=responses['orders'];by_tag={};ids=set();expected={};pending=[];fills={}
    for r in rows:
        tag=r.get('remark');oid=str(r.get('order_id',''))
        if tag not in requests or tag in by_tag or not oid or oid=='None' or oid in ids:
            raise SafetyError('Unknown or duplicate HK broker order')
        request,known=requests[tag]
        if (r.get('code')!=request['code'] or r.get('trd_side')!=request['side']
            or amount(r.get('qty'))!=amount(request['qty'])
            or amount(r.get('price'))!=amount(request['price'])
            or (known is not None and known!=oid)):
            raise SafetyError('HK order identity differs from intent')
        qty,filled,average=map(amount,(r.get('qty'),r.get('dealt_qty'),r.get('dealt_avg_price')))
        status=r.get('order_status')
        if (not isinstance(status,str) or not status or filled<0 or filled>qty
            or filled!=filled.to_integral_value() or (filled>0 and average<=0)
            or (status=='FILLED_ALL' and filled!=qty)
            or (status=='CANCELLED_PART' and not 0<filled<qty)
            or (status in TERMINAL-{'FILLED_ALL','CANCELLED_PART'} and filled!=0)):
            raise SafetyError('Invalid HK filled quantity or status')
        if filled and ((request['side']=='BUY' and average>amount(request['price']))
                       or (request['side']=='SELL' and average<amount(request['price']))):
            raise SafetyError('HK fill outside limit price')
        code=request['code'];expected[code]=expected.get(code,amount(0))+filled*(1 if request['side']=='BUY' else -1)
        if status not in TERMINAL:pending.append(oid)
        fills[tag]=dict(order_id=oid,qty=str(filled),average_hkd=str(average),status=status)
        by_tag[tag]=r;ids.add(oid)
    if set(by_tag)!=set(requests):raise SafetyError('Missing HK submission in complete broker history')
    actual={}
    for row in responses['positions']:
        code=row.get('code');qty=amount(row.get('qty'))
        if not isinstance(code,str) or not code.startswith('HK.') or code in actual or qty<0 or qty!=qty.to_integral_value():
            raise SafetyError('Invalid or duplicate HK broker position')
        actual[code]=qty
    expected={k:v for k,v in expected.items() if v};actual={k:v for k,v in actual.items() if v}
    if any(q<0 for q in expected.values()) or expected!=actual:
        raise SafetyError('HK position differs from strategy fills')
    return dict(account=journal.account,as_of=snapshot['checked_at'],inventory_reconciled=True,
                positions={k:str(v) for k,v in actual.items()},fills=fills,
                pending_order_ids=sorted(pending),pending_or_unknown=bool(pending),
                fees_reconciled=False,orders_enabled=False)
