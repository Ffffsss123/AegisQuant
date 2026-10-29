"""Strict HK account/funds admission checks; no SDK mutations or order submission."""
from datetime import datetime,timedelta
from .hk_policy import amount
from .policy import SafetyError


def _account_and_time(accounts, account, captured_at, now):
    if not account:raise SafetyError('Explicit HK simulation account required')
    matches=[a for a in accounts if str(a.get('acc_id'))==str(account)]
    if len(matches)!=1:raise SafetyError('HK account missing or ambiguous')
    a=matches[0]
    if a.get('trd_env')!='SIMULATE' or a.get('acc_status')!='ACTIVE' or a.get('trdmarket_auth')!=['HK']:
        raise SafetyError('Active HK-only simulation account required')
    try:
        stamp=datetime.fromisoformat(captured_at)
        if now.tzinfo is None or stamp.tzinfo is None or not timedelta(0)<=now-stamp<=timedelta(seconds=30):raise ValueError()
    except (ValueError,TypeError):raise SafetyError('Fresh HK funds snapshot required')
    return a


def validate_snapshot(accounts, funds, *, account, captured_at, now):
    _account_and_time(accounts, account, captured_at, now)
    if len(funds)!=1:raise SafetyError('Single HK funds row required')
    row=funds[0]
    # Deprecated aggregate cash / margin buying power must never replace these.
    cash=amount(row.get('hk_cash'))
    withdrawable=amount(row.get('hk_avl_withdrawal_cash'))
    cash_power=amount(row.get('hkd_net_cash_power'))
    if min(cash,withdrawable,cash_power)<0:raise SafetyError('Negative HK cash or debt')
    # Explicit HK-denominated fields remain valid even if aggregate currency is N/A.
    # This conservative cap is an input to shared-pool checks, not trade permission.
    return dict(account_suffix=str(account)[-4:],env='SIMULATE',currency='HKD',
                cash_cap_hkd=str(min(cash,withdrawable,cash_power)),
                captured_at=captured_at,orders_enabled=False,
                status='cash_fields_validated_shared_reservation_still_required')


def validate_flat_paper_snapshot(accounts, snapshot, *, account, now):
    """Validate an empty HK stock simulator baseline, never real settled cash.

    AU paper rules specify HK virtual assets in HKD (support/topic6_437).
    Single-market accounts ignore the funds display-currency argument. This
    narrow path only accepts a reconciled flat CASH simulator; it does not
    replace validate_snapshot or authorize trading / increase shared capital.
    """
    a=_account_and_time(accounts, account, snapshot.get('checked_at'), now)
    if a.get('acc_type')!='CASH' or a.get('sim_acc_type')!='STOCK':
        raise SafetyError('HK stock cash simulator required')
    responses={}
    for name in ('funds','positions','orders'):
        item=snapshot.get(name,{})
        if type(item.get('return_code')) is not int or item['return_code']!=0 or not isinstance(item.get('response'),list):
            raise SafetyError('Successful funds, positions and orders queries required')
        responses[name]=item['response']
    if responses['positions'] or responses['orders'] or len(responses['funds'])!=1:
        raise SafetyError('Empty HK simulator baseline required')
    row=responses['funds'][0]
    if row.get('currency') not in ('HKD','N/A'):
        raise SafetyError('Conflicting simulator currency')
    cash=amount(row.get('cash'))
    if (cash<0 or amount(row.get('total_assets'))!=cash
        or any(amount(row.get(k))!=0 for k in ('frozen_cash','market_val','interest_charged_amount'))):
        raise SafetyError('Simulator cash baseline does not reconcile')
    return dict(account_suffix=str(account)[-4:],env='SIMULATE',currency='HKD',
                virtual_cash_hkd=str(cash),captured_at=snapshot['checked_at'],
                status='flat_virtual_cash_baseline_verified',orders_enabled=False,
                real_settled_cash_verified=False,shared_budget_credit_usd='0')


def validate_paper_cash(accounts,snapshot,inventory,*,account,now):
    """HK-only CASH simulator balance, never real settled funds or extra capital."""
    a=_account_and_time(accounts,account,snapshot.get('checked_at'),now)
    if (a.get('acc_type')!='CASH' or a.get('sim_acc_type')!='STOCK'
        or snapshot.get('account')!=str(account) or snapshot.get('env')!='SIMULATE'
        or inventory.get('account')!=str(account) or inventory.get('inventory_reconciled') is not True
        or inventory.get('as_of')!=snapshot.get('checked_at')):
        raise SafetyError('Same-snapshot HK simulator inventory required')
    funds=snapshot.get('funds',{})
    if type(funds.get('return_code')) is not int or funds['return_code']!=0 or not isinstance(funds.get('response'),list) or len(funds['response'])!=1:
        raise SafetyError('Successful single-row HK simulator funds query required')
    row=funds['response'][0]
    if row.get('currency') not in ('HKD','N/A'):raise SafetyError('Conflicting HK simulator currency')
    cash,frozen,assets,value,interest=map(amount,(row.get(k) for k in ('cash','frozen_cash','total_assets','market_val','interest_charged_amount')))
    if min(cash,frozen,assets,value)<0 or interest!=0 or abs(assets-cash-value)>amount('.01'):
        raise SafetyError('HK virtual assets do not reconcile or indicate debt')
    held=any(amount(q)>0 for q in inventory.get('positions',{}).values())
    if (held and value<=0) or (not held and value!=0):raise SafetyError('HK virtual market value conflicts with inventory')
    # Subtract frozen funds conservatively; never use margin buying power.
    return dict(account_suffix=str(account)[-4:],env='SIMULATE',currency='HKD',
                cash_cap_hkd=str(max(amount(0),cash-frozen)),captured_at=snapshot['checked_at'],
                status='reconciled_virtual_cash_only',real_settled_cash_verified=False,
                shared_budget_credit_usd='0',orders_enabled=False)
