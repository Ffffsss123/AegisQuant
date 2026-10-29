"""Fixed, long-only 1–5 session simulation rules; no fitted parameters."""
from math import floor
from .policy import trend,number,SafetyError

VERSION='swing_1_5_v1'
PARAMETERS=dict(version=VERSION,max_sessions=5,atr_period=14,stop_atr=1.5,target_atr=3.0,
                risk_fraction=.025,breakout_sessions=5,entry_window='09:35–09:40 America/New_York',
                exit_minutes_before_close=15)

SELECTION_PROFILE='volatile_trend_v1'
SELECTION_PARAMETERS=dict(atr_pct_min=.02,atr_pct_max=.08,efficiency_min=.30)

def features(bars,day):
    base=trend(bars,day)
    true_ranges=[max(b['high']-b['low'],abs(b['high']-bars[i-1]['close']),abs(b['low']-bars[i-1]['close']))
                 for i,b in enumerate(bars) if i>0]
    atr=sum(true_ranges[-14:])/14
    breakout=bars[-1]['close']>max(b['high'] for b in bars[-6:-1])
    closes=[b['close'] for b in bars]
    travel=sum(abs(closes[i]-closes[i-1]) for i in range(len(closes)-20,len(closes)))
    efficiency=abs(closes[-1]-closes[-21])/travel if travel else 0.
    atr_pct=atr/closes[-1]
    trend_clear=closes[-1]>closes[-21] and sum(closes[-5:])>sum(closes[-10:-5]) and efficiency>=.30
    volatile_trend=trend_clear and .02<=atr_pct<=.08
    return dict(base,atr_pct=atr_pct,trend_efficiency=efficiency,volatile_trend=volatile_trend,selection_score=atr_pct*efficiency,base_buy=base['buy'],buy=base['buy'] and breakout and atr>0,atr=atr,
                stop_distance=1.5*atr,target_distance=3*atr,breakout=breakout)

def snapshot(scan,histories,selection_profile="legacy"):
    if selection_profile not in ("legacy",SELECTION_PROFILE):raise SafetyError("Unknown selection profile")
    signals={};errors={}
    for code in scan.get('signals',{}):
        try:signals[code]=features(histories[code],scan['date'])
        except (KeyError,ValueError,TypeError,SafetyError) as exc:errors[code]=str(exc)
    # Only the verified parent candidate universe can produce a new entry.
    eligible={c['code'] for c in scan.get('candidates',[])}
    selected=[dict(code=c,**s) for c,s in signals.items() if c in eligible and s['buy'] and s['liquid']]
    if selection_profile==SELECTION_PROFILE:
        selected=[c for c in selected if c['volatile_trend']]
        selected.sort(key=lambda c:(-c['selection_score'],-c['strength'],c['code']))
    else:selected.sort(key=lambda c:(-c['strength'],c['code']))
    return dict(selection_profile=selection_profile,selection_parameters=SELECTION_PARAMETERS if selection_profile==SELECTION_PROFILE else {},version=VERSION,date=scan['date'],source_generated_at=scan['generated_at'],
                complete=bool(scan.get('complete')) and not errors,signals=signals,candidates=selected,
                errors=errors,parameters=PARAMETERS)

def size(cash,initial,price,signal,fee=3,allocation=.45,remaining_risk=None):
    distance=number(signal['stop_distance']);target=number(signal['target_distance'])
    if price<=0 or distance<=0 or target<=0 or distance>=price:return 0
    # Reserve both fees and an additional 0.15% per side for spread/slippage stress.
    friction=price*.003
    risk=min(initial*.025,remaining_risk if remaining_risk is not None else initial*.025)
    qty=min(floor(max(0,min(cash,initial*allocation)-fee)/price),
            floor(max(0,risk-2*fee)/(distance+friction)))
    if qty<=0 or target*qty<=2*fee+friction*qty:return 0
    return qty

def exit_reason(position,signal,bid,session_count,near_close):
    if bid<=position['swing_stop']:return 'stop'
    if bid>=position['swing_target']:return 'target'
    if signal and signal['sell']:return 'trend_reversal'
    if session_count>=5 and near_close:return 'max_sessions'
    return None
