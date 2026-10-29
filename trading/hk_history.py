"""Completed HK daily signals; no account access or order authorization."""
from datetime import date,datetime,time
from .hk_policy import HK,amount
from .hk_fx import validate_reference
from .policy import SafetyError,trend


def evaluate(rows,calendar,expected,fx,now):
    if now.tzinfo is None:raise SafetyError('HK history clock requires timezone')
    sessions=[]
    for r in calendar:
        day=date.fromisoformat(r['time'])
        if r.get('trade_date_type') not in ('WHOLE','MORNING'):
            raise SafetyError('Unverified HK calendar type')
        if day.isoformat()<=expected:sessions.append(r)
    dates=[r['time'] for r in sessions]
    if dates!=sorted(set(dates)) or len(dates)<60 or dates[-1]!=expected:
        raise SafetyError('Incomplete HK history calendar')
    close=time(16,10) if sessions[-1]['trade_date_type']=='WHOLE' else time(12,10)
    if now.astimezone(HK)<datetime.combine(date.fromisoformat(expected),close,HK):
        raise SafetyError('HK closing auction has not completed')
    if [r.get('date') for r in rows][-60:]!=dates[-60:]:
        raise SafetyError('HK daily history has gaps or is stale')
    rate=validate_reference(fx,now)
    converted=[]
    for r in rows:
        b=dict(r)
        for key in ('open','high','low','close','turnover'):b[key]=float(amount(r[key])/rate)
        converted.append(b)
    signal=trend(converted,expected)
    # HK execution still uses HKD; dollar conversion is only for common floors.
    signal.update(fast_hkd=float(amount(signal['fast'])*rate),
                  slow_hkd=float(amount(signal['slow'])*rate),
                  hkd_per_usd=str(rate),orders_enabled=False,
                  status='history_validated_security_and_admission_pending')
    return signal
