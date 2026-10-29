"""Offline HK stock validation only; does not bind accounts or submit orders.

Hours: HKEX Securities Market trading FAQ. Auctions and extended lunch trading
are intentionally excluded. Prices/board lots must come from current metadata.
"""
from datetime import datetime, date, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from zoneinfo import ZoneInfo
from .policy import SafetyError

HK=ZoneInfo('Asia/Hong_Kong')

def amount(value):
    try:
        if isinstance(value,bool):raise ValueError()
        v=Decimal(str(value))
        if not v.is_finite():raise ValueError()
        return v
    except (InvalidOperation,ValueError,TypeError):raise SafetyError('Invalid HK amount')

def execution_session(rows,now):
    if now.tzinfo is None:raise SafetyError('HK clock requires timezone')
    local=now.astimezone(HK);day=local.date().isoformat()
    matches=[r for r in rows if r.get('time')==day]
    if not matches:return None
    if len(matches)!=1:raise SafetyError('Duplicate HK calendar date')
    kind=matches[0].get('trade_date_type')
    if kind not in ('WHOLE','MORNING'):raise SafetyError('Unsupported HK session type')
    def at(h,m=0):return datetime.combine(date.fromisoformat(day),time(h,m),HK)
    # Existing opening volatility buffer; no midday or auction order submissions.
    if at(9,35)<=local<at(12):return day
    if kind=='WHOLE' and at(13)<=local<at(16):return day
    return None

def board_order(code,qty,price,lot_size,tick_size):
    if not isinstance(code,str) or not code.startswith('HK.') or not code[3:].isdigit():raise SafetyError('HK code required')
    q,p,lot,tick=map(amount,(qty,price,lot_size,tick_size))
    if q<=0 or lot<=0 or q!=q.to_integral_value() or lot!=lot.to_integral_value() or q%lot:raise SafetyError('Whole HK board lots required')
    if p<=0 or tick<=0 or p%tick:raise SafetyError('HK price is off verified tick grid')
    return q*p

def purchase_plan(code,price,lot_size,tick_size,available_usd,cash_hkd,roundtrip_fee_hkd,fx,now):
    """Diagnostic sizing, NOT a shared-pool reservation or trade authorization.

Caller must supply already-unreserved USD capital, verified cash and a complete
roundtrip fee estimate. No margin, FX conversion or default exchange rate.
"""
    if now.tzinfo is None:raise SafetyError('FX clock requires timezone')
    from .hk_fx import validate_reference
    rate=validate_reference(fx,now)
    available,cash,fee=map(amount,(available_usd,cash_hkd,roundtrip_fee_hkd))
    if available<0 or cash<0 or fee<=0:raise SafetyError('Cash and positive complete HK fees required')
    one=board_order(code,lot_size,price,lot_size,tick_size)
    spend=min(available*rate,cash)
    lots=max(0,int(((spend-fee)/one).to_integral_value(rounding=ROUND_FLOOR)))
    qty=lots*int(amount(lot_size));cost=one*lots+fee if lots else Decimal(0)
    return dict(code=code,qty=qty,cost_hkd=str(cost),reservation_usd=str(cost/rate),
                orders_enabled=False,reason='offline_preflight_only' if lots else 'insufficient_shared_cash_for_one_lot')
