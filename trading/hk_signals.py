"""Read complete prior-session HK signals; never authorizes an order."""
from datetime import datetime
from .hk_policy import execution_session, HK
from .policy import SafetyError
from .hk_scan import completed_session


def current_signals(scan, calendar, now):
    session=execution_session(calendar,now)
    if session is None:
        raise SafetyError('HK signal execution outside continuous session')
    dates=[r['time'] for r in calendar]
    if dates!=sorted(set(dates)) or any(r.get('trade_date_type') not in ('WHOLE','MORNING') for r in calendar):
        raise SafetyError('Unverified HK signal calendar')
    previous=[day for day in dates if day<session]
    if not previous:
        raise SafetyError('Previous HK trading session unavailable')
    expected=previous[-1]
    stamp=datetime.fromisoformat(scan['published_at'])
    if (stamp.tzinfo is None or stamp>now or stamp.astimezone(HK).date().isoformat()<expected
        or completed_session(calendar,stamp)!=expected
        or scan.get('date')!=expected or scan.get('complete') is not True
        or scan.get('errors') or scan.get('orders_enabled') is not False
        or not isinstance(scan.get('signals'),dict) or not isinstance(scan.get('candidates'),list)):
        raise SafetyError('Complete previous-session HK signals required')
    codes=[]
    for row in scan['candidates']:
        code=row.get('code');signal=scan['signals'].get(code,{})
        if (not isinstance(code,str) or len(code)!=8 or not code.startswith('HK.') or not code[3:].isdigit()
            or code in codes or signal.get('buy') is not True or signal.get('liquid') is not True
            or row.get('buy') is not True or row.get('liquid') is not True):
            raise SafetyError('HK candidate signal identity failed')
        codes.append(code)
    return dict(session=session,source_session=expected,candidates=scan['candidates'],
                signals=scan['signals'],orders_enabled=False)
