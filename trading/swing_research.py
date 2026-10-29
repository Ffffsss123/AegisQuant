"""Daily-OHLC approximation on the CURRENT universe, explicitly not intraday execution proof."""
import json
from collections import defaultdict
from pathlib import Path
from .swing_model import features,size,PARAMETERS
from .rolling_scan import atomic
from .policy import SafetyError


def signal_history(histories,days):
    offsets={c:{b['date']:i for i,b in enumerate(bars)} for c,bars in histories.items()}
    result={}
    for index,day in enumerate(days):
        if index<60:continue
        signals={};previous=days[index-1]
        for c,bars in histories.items():
            i=offsets[c].get(previous,-1)
            if i<59:continue
            prior=bars[i-59:i+1]
            if [b['date'] for b in prior]!=days[index-60:index]:continue
            try:signals[c]=features(prior,previous)
            except (KeyError,ValueError,SafetyError):continue
        result[day]=signals
    return result


def run(histories,days,mode,capital=600,fee=3,friction=.0015,signals_by_day=None,selection_profile="legacy",loss_limit=30):
    cash=capital;positions={};orders=[];curve=[];paused=False;peak=capital
    indexed={c:{b['date']:b for b in bars} for c,bars in histories.items()}
    signals_by_day=signal_history(histories,days) if signals_by_day is None else signals_by_day
    def sell(c,raw,reason,day):
        nonlocal cash
        p=positions.pop(c);cash+=p['qty']*raw*(1-friction)-fee
        orders.append(dict(day=day,code=c,side='SELL',qty=p['qty'],price=raw*(1-friction),fee=fee,reason=reason,holding_sessions=p['sessions']))
    def equity():return cash+sum(p['qty']*p['last'] for p in positions.values())
    for day in days:
        if day not in signals_by_day:continue
        signals=signals_by_day[day];held_at_open=set(positions)
        # Opening decisions see opening prices only, never today's high/low/close.
        for c,p in list(positions.items()):
            p['sessions']+=1;bar=indexed[c].get(day)
            if bar is None:paused=True;continue
            p['last']=bar['open'];sig=signals.get(c)
            if mode!='baseline' and bar['open']<=p['stop']:sell(c,bar['open'],'gap_stop',day)
            elif mode!='baseline' and bar['open']>=p['target']:sell(c,p['target'],'gap_target',day)
            elif sig and sig['sell']:sell(c,bar['open'],'trend',day)
        if capital-equity()>=loss_limit:paused=True
        candidates=[(c,s) for c,s in signals.items() if (s['base_buy'] if mode=='baseline' else s['buy'])
                    and s['liquid'] and c not in held_at_open and day in indexed[c]]
        if selection_profile=='volatile_trend_v1':
            candidates=[(c,s) for c,s in candidates if s.get('volatile_trend')]
            candidates.sort(key=lambda x:(-x[1]['selection_score'],-x[1]['strength'],x[0]))
        else:candidates.sort(key=lambda x:(-x[1]['strength'],x[0]))
        # Swing does not replace a held stock in the same opening decision window.
        slots=2-(len(held_at_open) if mode!='baseline' else len(positions))
        for c,s in ([] if paused else candidates[:max(0,slots)]):
            bar=indexed[c][day];price=bar['open']*(1+friction)
            if mode=='baseline':qty=int(max(0,min(cash,capital*.45)-fee)//price)
            else:
                open_risk=sum(p['qty']*max(0,p['entry']-p['stop'])+2*fee+p['qty']*p['entry']*.003 for p in positions.values())
                remaining=max(0,loss_limit-max(0,capital-equity())-open_risk)
                qty=size(cash,capital,price,s,fee,remaining_risk=remaining)
            if qty<=0:continue
            cash-=qty*price+fee
            positions[c]=dict(qty=qty,entry=price,last=bar['open'],sessions=1,stop=price-s['stop_distance'],target=price+s['target_distance'])
            orders.append(dict(day=day,code=c,side='BUY',qty=qty,price=price,fee=fee))
            if capital-equity()>=loss_limit:paused=True;break
        # Intraday barriers are evaluated only after all opening decisions.
        for c,p in list(positions.items()):
            bar=indexed[c].get(day)
            if bar is None:continue
            if mode!='baseline' and bar['low']<=p['stop']:sell(c,p['stop'],'stop',day)
            elif mode!='baseline' and bar['high']>=p['target']:sell(c,p['target'],'target',day)
            elif mode!='baseline' and p['sessions']>=5:sell(c,bar['close'],'max_sessions',day)
            else:p['last']=bar['close']
        value=equity();peak=max(peak,value)
        if capital-value>=loss_limit:paused=True
        curve.append(dict(day=day,equity=value,drawdown=1-value/peak))
    sells=[x for x in orders if x['side']=='SELL'];weeks={}
    from datetime import date,timedelta
    for row in curve:
        d=date.fromisoformat(row['day']);weeks[(d-timedelta(days=d.weekday())).isoformat()]=row['equity']
    prior=capital;weekly=[]
    for week,value in sorted(weeks.items()):weekly.append(dict(week=week,pnl_usd=value-prior,pnl_nzd=(value-prior)/.6));prior=value
    ending=curve[-1]['equity'] if curve else capital
    return dict(selection_profile=selection_profile,loss_limit_usd=loss_limit,mode=mode,friction_per_side=friction,initial_usd=capital,ending_equity_usd=ending,
        evaluation_start=curve[0]['day'] if curve else None,evaluation_end=curve[-1]['day'] if curve else None,
        pnl_nzd=(ending-capital)/.6,max_drawdown=max((r['drawdown'] for r in curve),default=0),
        fees_usd=fee*len(orders),orders=len(orders),roundtrips=len(sells),open_positions=len(positions),paused=paused,
        max_holding_sessions=max((o.get('holding_sessions',0) for o in sells),default=0),weekly=weekly,trade_log=orders)


def main():
    root=Path.cwd();market=root/'data/trading/market';scan=json.loads((market/'scan.json').read_text());histories={}
    # Include prior-day exclusions when real historical files exist; do not select today's winners only.
    cat=json.loads((market/('catalog_'+scan['date']+'.json')).read_text())
    from .scanner import classification
    from datetime import date
    universe=classification(cat['directory'],cat['basic'],{},date.fromisoformat(scan['date']))
    for c,m in universe.items():
        if not m['verified'] or m['lot']!=1:continue
        p=market/'history'/(c+'.json')
        if not p.exists():continue
        bars=json.loads(p.read_text());bars=[b for b in bars if b['date']<=scan['date']]
        if len(bars)>=60:histories[c]=bars
    # Session dates come from the official calendar cached by the daily scan validation.
    from .rolling_scan import DailyBroker
    from datetime import datetime,timezone
    b=DailyBroker()
    try:days=[r['time'] for r in b.calendar(datetime.now(timezone.utc)) if r['time']<=scan['date']]
    finally:b.close()
    earliest=min((bars[0]['date'] for bars in histories.values()),default=scan['date']);days=[d for d in days if d>=earliest]
    signals=signal_history(histories,days)
    results=[run(histories,days,mode,friction=friction,signals_by_day=signals) for friction in (.0015,.003) for mode in ('baseline','swing')]
    report=dict(version=PARAMETERS['version'],parameters=PARAMETERS,source='moomoo real historical daily cache',as_of=scan['date'],
        symbols=len(histories),sessions=len(days),results=results,limitations=[
        'Current listed universe only: delisted names and historical membership unavailable; survivorship and cache coverage bias.',
        'Daily open substitutes for 09:35 execution; OHLC cannot prove limit fills, spread, queue position, or intrabar event order.',
        'Stop first if both stop and target touched. Fifth-session close substitutes for the 15-minute-before-close exit.',
        'Forward-adjusted daily candles are a price proxy, not raw executable historical quotes; corporate actions were not independently audited.',
        'No historical news filter replay or historical suspension classifications; not complete strategy acceptance.',
        'Parameters fixed before running; recent data comparison is diagnostic, not independent out-of-sample proof.',
        'USD 3 per order is the current simulation fee reserve, not verified broker pricing. No tax/dividend cash flows.'])
    atomic(root/'data/trading/swing_research.json',report)
    from .swing_model import snapshot
    profile=json.loads((market.parent/'selection_profile.json').read_text()).get('profile','legacy') if (market.parent/'selection_profile.json').exists() else 'legacy'
    atomic(market/'short_scan.json',snapshot(scan,histories,profile))
    print(json.dumps({k:report[k] for k in ('as_of','symbols','sessions')},ensure_ascii=False))
    for r in results:print({k:v for k,v in r.items() if k not in ('trade_log','weekly')})

if __name__=='__main__':main()
