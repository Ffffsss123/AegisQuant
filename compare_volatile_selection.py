import json
from pathlib import Path
from datetime import datetime,timezone
from trading.swing_model import snapshot,SELECTION_PROFILE,size
from trading.swing_research import signal_history,run
from trading.rolling_scan import atomic,DailyBroker
root=Path.cwd();market=root/'data/trading/market';scan=json.loads((market/'scan.json').read_text());histories={}
for code in scan['signals']:
 p=market/'history'/(code+'.json')
 if p.exists():
  bars=[b for b in json.loads(p.read_text()) if b['date']<=scan['date']]
  if len(bars)>=60:histories[code]=bars
b=DailyBroker()
try:days=[r['time'] for r in b.calendar(datetime.now(timezone.utc)) if r['time']<=scan['date']]
finally:b.close()
start=min(b[0]['date'] for b in histories.values());days=[d for d in days if d>=start]
signals=signal_history(histories,days)
results=[]
for profile in ['legacy',SELECTION_PROFILE]:
 for friction in [.0015,.003]:
  r=run(histories,days,'swing',capital=500,fee=3,friction=friction,signals_by_day=signals,selection_profile=profile,loss_limit=100)
  results.append(r)
selected=snapshot(scan,histories,SELECTION_PROFILE)
report=dict(as_of=scan['date'],symbols=len(histories),sessions=len(days),results=results,candidate_count=len(selected['candidates']),limitations=['Current surviving cached signal universe, not point-in-time or delisting-complete.','Daily OHLC approximate fills, stop first on ambiguous bars. No historical news/spread/limit-fill validation.','Same cache used before; not out-of-sample evidence. USD3 per-side fee reserve plus spread/slippage stress.','Selected using completed daily bars; not minute-level trend or guaranteed intraday exit.'])
atomic(root/'data/trading/volatile_selection_research.json',report)
atomic(root/'data/trading/market/volatile_selection_preview.json',selected)
print('coverage',len(histories),len(days),'candidates',len(selected['candidates']))
for r in results:print({k:r[k] for k in ['selection_profile','friction_per_side','ending_equity_usd','max_drawdown','fees_usd','roundtrips','paused']})
print('top',[{k:c[k] for k in ['code','atr_pct','trend_efficiency','selection_score']} for c in selected['candidates'][:5]])
