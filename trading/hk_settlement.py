"""Terminal HK buy settlement into the shared reservation journal.

Caller holds shared admission lock and invalidates the old risk snapshot before
mutating this journal. Fee evidence is explicit: broker fees or estimates using
all actual executions. Estimates never become real-account fee verification.
"""
import json
from datetime import datetime
from .hk_policy import amount
from .hk_fees import order_estimate,order_upper_bound
from .policy import SafetyError
from .engine import TERMINAL


def fee_amount(record,notional,filled_qty=None):
    if not isinstance(record,dict) or not isinstance(record.get('evidence'),str) or not record['evidence'].strip():
        raise SafetyError('Explicit HK execution fee evidence required')
    value=amount(record.get('amount_hkd'))
    if value<0:raise SafetyError('Negative HK fee')
    if record.get('source')=='broker':return value
    if record.get('source')=='simulation_order_upper_bound':
        if amount(record.get('filled_qty'))!=amount(filled_qty):
            raise SafetyError('Fee reserve quantity differs from order fill')
        expected=amount(order_upper_bound(notional,filled_qty)['total_hkd'])
        if value!=expected:raise SafetyError('Order-based fee reserve differs from upper bound')
        return value
    if record.get('source')!='simulation_estimate':raise SafetyError('Unsupported HK fee source')
    executions=record.get('execution_amounts_hkd')
    if not isinstance(executions,list) or not executions:raise SafetyError('Actual executions required for fee rounding')
    values=[amount(v) for v in executions]
    # Broker averages may be rounded; no unexplained notional discrepancy allowed.
    if abs(sum(values)-notional)>amount('.01'):raise SafetyError('Execution fee amounts differ from fills')
    estimate=amount(order_estimate(values)['total_hkd'])
    if value!=estimate:raise SafetyError('Simulation fee estimate differs from published policy')
    return value


def settle_buys(submissions,reservations,inventory,fee_records,*,fixed_hkd_per_usd,now):
    if (inventory.get('inventory_reconciled') is not True
        or inventory.get('account')!=submissions.account):
        raise SafetyError('Reconciled HK inventory required')
    try:
        stamp=datetime.fromisoformat(inventory['as_of'])
        if stamp.tzinfo is None or now.tzinfo is None or not 0<=(now-stamp).total_seconds()<=30:raise ValueError()
    except (KeyError,TypeError,ValueError):raise SafetyError('Fresh inventory required for HK settlement')
    rate=amount(fixed_hkd_per_usd)
    if rate<=0:raise SafetyError('Verified fixed accounting FX required')
    # Validate all fill and fee inputs before updating any reservation.
    plan=[]
    for tag,body,known_id in submissions.db.execute('SELECT tag,request,order_id FROM submissions'):
        request=json.loads(body)
        if request['side']!='BUY':continue
        fill=inventory.get('fills',{}).get(tag)
        if not fill or (known_id is not None and fill['order_id']!=known_id):
            raise SafetyError('Missing or conflicting HK fill attribution')
        status=fill['status'];qty=amount(fill['qty'])
        if status not in TERMINAL:continue
        if qty==0:
            plan.append(('release',tag,fill,None));continue
        notional=qty*amount(fill['average_hkd'])
        fee=fee_amount(fee_records.get(fill['order_id']),notional,qty)
        plan.append(('fill',tag,fill,(notional+fee)/rate))
    result=[]
    for action,tag,fill,debit in plan:
        if action=='release':
            reservations.release_unfilled(tag,broker_status=fill['status'],filled_qty=0,
                reconciled=True,evidence='HK inventory '+inventory['as_of'])
        else:
            closed=reservations.db.execute('SELECT 1 FROM closed_positions WHERE key=?',(tag,)).fetchone()
            if closed:
                prior=reservations.db.execute('SELECT debit,qty,status FROM settled_fills WHERE key=?',(tag,)).fetchone()
                if not prior or (amount(prior[0]),amount(prior[1]),prior[2])!=(debit,amount(fill['qty']),fill['status']):
                    raise SafetyError('Closed HK entry differs from repeated fill evidence')
                result.append(dict(tag=tag,transition='already_closed',orders_enabled=False))
                continue
            reservations.settle_fill(tag,debit_usd=debit,filled_qty=fill['qty'],
                broker_status=fill['status'],reconciled=True,
                evidence=json.dumps(dict(inventory_as_of=inventory['as_of'],order_id=fill['order_id'],
                    fee=fee_records[fill['order_id']],fixed_hkd_per_usd=str(rate))))
        result.append(dict(tag=tag,transition=action,orders_enabled=False))
    return result


def settle_closes(submissions,reservations,inventory,fee_records,*,fixed_hkd_per_usd,now):
    """Release only fully sold entries; partial exits retain committed capital."""
    if inventory.get('inventory_reconciled') is not True or inventory.get('account')!=submissions.account:
        raise SafetyError('Reconciled HK inventory required')
    from .hk_admission import fresh
    fresh(inventory.get('as_of'),now)
    if inventory.get('pending_or_unknown') is not False:return []
    rate=amount(fixed_hkd_per_usd)
    if rate<=0:raise SafetyError('Verified fixed accounting FX required')
    requests={tag:json.loads(body) for tag,body in submissions.db.execute('SELECT tag,request FROM submissions')}
    grouped={}
    for tag,request in requests.items():
        if request['side']!='SELL':continue
        key=request.get('position_key');entry=requests.get(key,{})
        if entry.get('side')!='BUY' or entry.get('code')!=request['code']:
            raise SafetyError('HK closing order lacks matching entry')
        fill=inventory.get('fills',{}).get(tag)
        if not fill or fill['status'] not in TERMINAL:raise SafetyError('HK close is not terminal')
        if amount(fill['qty'])>0:grouped.setdefault(key,[]).append(fill)
    results=[]
    for key,fills in grouped.items():
        entry=reservations.db.execute('SELECT qty,evidence FROM settled_fills WHERE key=?',(key,)).fetchone()
        if not entry:raise SafetyError('HK entry cost not settled')
        try:entry_rate=amount(json.loads(entry[1])['fixed_hkd_per_usd'])
        except (TypeError,ValueError,KeyError):raise SafetyError('Entry fixed FX evidence missing')
        if entry_rate!=rate:raise SafetyError('Closing FX differs from entry accounting FX')
        qty=sum((amount(f['qty']) for f in fills),amount(0))
        if qty>amount(entry[0]):raise SafetyError('HK exit exceeds entry quantity')
        if qty<amount(entry[0]):continue
        proceeds=amount(0);ids=[]
        for fill in fills:
            gross=amount(fill['qty'])*amount(fill['average_hkd'])
            proceeds+=gross-fee_amount(fee_records.get(fill['order_id']),gross,fill['qty'])
            ids.append(fill['order_id'])
        result=reservations.settle_closed_position(key,net_proceeds_usd=proceeds/rate,
            sold_qty=qty,order_ids=ids,position_flat=True,no_pending_orders=True,reconciled=True,
            evidence=json.dumps(dict(inventory_as_of=inventory['as_of'],fee_records={oid:fee_records[oid] for oid in ids},fixed_hkd_per_usd=str(rate))))
        results.append(result)
    return results


def simulated_order_fee_records(inventory, *, include_pending=False):
    """Explicit conservative model when paper APIs omit individual deal records."""
    if inventory.get('inventory_reconciled') is not True or not inventory.get('account'):
        raise SafetyError('Reconciled simulation orders required')
    records={}
    for fill in inventory.get('fills',{}).values():
        if (fill['status'] not in TERMINAL and not include_pending) or amount(fill['qty'])==0:continue
        qty=amount(fill['qty']);notional=qty*amount(fill['average_hkd'])
        estimate=order_upper_bound(notional,qty)
        records[fill['order_id']]=dict(source='simulation_order_upper_bound',
            amount_hkd=estimate['total_hkd'],filled_qty=str(qty),model=estimate['model'],
            broker_confirmed=False,evidence='Reconciled cumulative order '+fill['order_id']+' at '+inventory['as_of'])
    return records
