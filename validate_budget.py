"""NZD budget scenario; constant reference FX, not historical NZD performance."""
import json
from datetime import datetime, timezone
from pathlib import Path
from backtest import load_prices,run,write_csv
from validate_history import benchmark
root=Path(__file__).resolve().parent
fx=json.loads((root/'data/real/NZDUSD_reference.json').read_text())['chart']['result'][0]['meta']
rate=fx['regularMarketPrice']
assert fx['symbol']=='NZDUSD=X' and fx['currency']=='USD' and 0<rate<2
stamp=datetime.fromtimestamp(fx['regularMarketTime'],timezone.utc).isoformat()
rows=load_prices(root/'data/real/SPY_2020_2025.csv')
out=root/'data/real/nzd5000';out.mkdir(exist_ok=True)
results=[]
for fast,slow in [(5,10),(5,20),(10,20),(20,60)]:
 s,curve,trades=run(rows,fast=fast,slow=slow,capital=5000*rate)
 # Peak-to-trough money loss corresponding to the percentage max drawdown.
 peak=5000*rate; worst=0; corresponding_loss=0
 for p in curve:
  peak=max(peak,p['equity'])
  dd=1-p['equity']/peak
  if dd>worst:
   worst=dd;corresponding_loss=(peak-p['equity'])/rate
 results.append(dict(fast=fast,slow=slow,summary=s,final_nzd=s['final_equity']/rate,drawdown_money_nzd=corresponding_loss))
 write_csv(out/f'{fast}_{slow}_equity.csv',curve,['date','equity','cash','shares','drawdown'])
 write_csv(out/f'{fast}_{slow}_trades.csv',trades,['date','side','qty','price','fee'])
base=benchmark(rows,capital=5000*rate)
lines=['# 5,000 纽币预算回测情景','',f'参考汇率：1 NZD = {rate:.6f} USD；行情时间 {stamp}。本金换算为 {5000*rate:.2f} USD。',
'来源：https://finance.yahoo.com/quote/NZDUSD=X/ ，原始响应保存在 data/real/NZDUSD_reference.json。','',
'**固定参考汇率情景，不是实际历史纽币回报。** 将同一个汇率用于初始本金和所有结果的换算，不计换汇点差、手续费、税务或历史汇率波动。', '',
'SPY 2020-01-02 至 2025-12-31 日线，整股，只做多，前日收盘信号、次日开盘成交，每边手续费和滑点各 5 基点。未计分红。', '',
'| 均线 | 累计价格收益 | 期末等值 NZD | 最大回撤 | 该回撤对应峰谷损失 NZD |', '|---|---:|---:|---:|---:|']
for r in results:
 s=r['summary'];lines.append(f"| {r['fast']}/{r['slow']} | {s['total_return']:.2%} | {r['final_nzd']:.2f} | {s['max_drawdown']:.2%} | {r['drawdown_money_nzd']:.2f} |")
lines+=['',f"买入持有：累计价格收益 {base['total_return']:.2%}，期末等值 NZD {base['final_equity']/rate:.2f}。",'',
'回撤金额按当时账户高点至低点计算，并非初始 5,000 乘以最大回撤率。较小本金只能购买整数股，闲置现金比例变化，因此收益率与此前 10,000 美元回测不同。','',
'5/20 已在同一段历史上比较过，结果不能当作独立样本外证明。仅供预算和软件验证，不能视为未来收益预测。', '', '复算：`.venv/bin/python validate_budget.py`。']
(out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
(out/'results.json').write_text(json.dumps(dict(initial_nzd=5000,rate=rate,fx_time=stamp,results=results,benchmark=base),ensure_ascii=False,indent=2))
print('\n'.join(lines))
