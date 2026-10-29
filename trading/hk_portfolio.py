"""Offline cross-market risk aggregation. No account access or order authorization.

The US/options equity input is the existing *single* combined strategy equity.
HK contributes P/L, commitments and fees, never its broker's nominal cash balance.
Amounts in the HK snapshot must already be reconciled to strategy-only activity.
"""
from datetime import datetime, timedelta
from .hk_policy import amount
from .policy import SafetyError


def aggregate(base, hk, fx, now):
    if now.tzinfo is None:raise SafetyError('Portfolio clock requires timezone')
    if (base.get('env')!='SIMULATE' or hk.get('env')!='SIMULATE'
        or not base.get('portfolio_id') or base['portfolio_id']!=hk.get('portfolio_id')
        or not base.get('account') or not hk.get('account') or hk['account']!=base.get('hk_account')
        or base.get('currency')!='USD' or hk.get('currency')!='HKD'
        or base.get('account')==hk['account']):
        raise SafetyError('Cross-market simulation identity mismatch')
    for record,age in ((base,30),(hk,30)):
        try:
            stamp=datetime.fromisoformat(record['as_of'])
            if stamp.tzinfo is None or not timedelta(0)<=now-stamp<=timedelta(seconds=age):raise ValueError()
        except (KeyError,TypeError,ValueError):raise SafetyError('Unverified or stale cross-market snapshot')
    if hk.get('reconciled') is not True or base.get('reconciled') is not True:
        raise SafetyError('Cross-market reconciliation required')
    from .hk_fx import validate_reference
    rate=validate_reference(fx,now)
    budget=amount(base.get('budget_usd'));loss=amount(base.get('loss_limit_usd'))
    if rate<=0 or budget<=0 or budget>500 or loss<=0 or loss>100:raise SafetyError('Cross-market budget or FX invalid')
    cash=amount(base.get('available_usd'));equity=amount(base.get('combined_equity_usd'))
    cost=amount(hk.get('position_cost_hkd'));value=amount(hk.get('position_value_hkd'))
    reserved=amount(hk.get('buy_reservation_hkd'));exit_fees=amount(hk.get('exit_fee_reserve_hkd'))
    fees=amount(hk.get('fees_hkd'));realized=amount(hk.get('realized_pnl_hkd'))
    if min(cash,cost,value,reserved,exit_fees,fees)<0:raise SafetyError('Negative cross-market commitment')
    # Realized P/L is gross; actual/estimated charged fees counted exactly once.
    pnl=(realized+value-cost-fees)/rate
    combined=equity+pnl
    # Do not allow HK gains to enlarge the original spending ceiling.
    available=max(amount(0),min(cash,budget)-(cost+reserved+exit_fees+fees+max(amount(0),-realized))/rate)
    pending=base.get('pending_or_unknown') is not False or hk.get('pending_or_unknown') is not False
    stopped=budget-combined>=loss
    return dict(combined_equity_usd=str(combined),hk_pnl_usd=str(pnl),
                available_usd=str(0 if pending or stopped else available),
                loss_stop=stopped,reconciliation_block=pending,orders_enabled=False,
                status='offline_risk_validation_only')
