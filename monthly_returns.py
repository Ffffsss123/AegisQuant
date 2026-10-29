"""Monthly mark-to-market statements, constant FX; no monthly liquidation."""
import csv
import json
from collections import OrderedDict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/real/nzd5000'

def monthly(rows, initial_usd, rate):
    months=OrderedDict()
    for row in rows:
        months.setdefault(row['date'][:7],[]).append(row)
    previous=initial_usd
    result=[]
    for month,group in months.items():
        last=group[-1]
        ending=float(last['equity'])
        result.append(dict(month=month,last_trading_day=last['date'],
            starting_equity_nzd=previous/rate,ending_equity_nzd=ending/rate,
            pnl_nzd=(ending-previous)/rate,return_pct=(ending/previous-1)*100,
            cash_nzd=float(last['cash'])/rate,shares=int(last['shares'])))
        previous=ending
    return result


def main():
    meta=json.loads((OUT/'results.json').read_text())
    rate=meta['rate']
    rows=list(csv.DictReader((OUT/'5_20_equity.csv').open(encoding='utf-8-sig')))
    results=monthly(rows,meta['initial_nzd']*rate,rate)
    assert len(results)==72
    assert abs(sum(r['pnl_nzd'] for r in results)-(results[-1]['ending_equity_nzd']-meta['initial_nzd']))<1e-7
    compound=1
    for r in results: compound*=1+r['return_pct']/100
    assert abs(compound-results[-1]['ending_equity_nzd']/meta['initial_nzd'])<1e-9
    with (OUT/'monthly_5_20.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    best=max(results,key=lambda x:x['return_pct']);worst=min(results,key=lambda x:x['return_pct'])
    wins=sum(r['pnl_nzd']>1e-8 for r in results)
    losses=sum(r['pnl_nzd']<-1e-8 for r in results)
    flat=len(results)-wins-losses
    lines=['# 5,000 纽币：按月查看回测收益','',
        '策略：SPY 5/20 日均线。2020—2025 年真实历史日线，共 72 个月。', '',
        '**月末估值报表，不是每月卖出、提现或分红。** 资金连续滚动，持仓仍在，盈亏包含未实现盈亏。', '',
        '每月盈亏 = 本月最后交易日账户权益 − 上月最后交易日账户权益；首月起点为初始本金。收益率 = 每月盈亏 / 月初权益。', '',
        f"固定换算汇率：1 NZD = {rate} USD；未模拟历史汇率、税、分红或换汇成本。交易手续费和滑点已按此前假设计入。", '',
        f'盈利月 {wins} 个，亏损月 {losses} 个，持平月 {flat} 个。',
        f"最好月份（按收益率）：{best['month']}，{best['return_pct']:+.2f}%，NZD {best['pnl_nzd']:+.2f}。",
        f"最差月份（按收益率）：{worst['month']}，{worst['return_pct']:+.2f}%，NZD {worst['pnl_nzd']:+.2f}。", '',
        '这是已查看过的历史策略，不能据此假设每月都有收入。本策略先等待 20 根日线形成慢均线，首月部分时间空仓。', '',
        '| 月份 | 月初资产 NZD | 当月盈亏 NZD | 当月收益率 | 月末资产 NZD |', '|---|---:|---:|---:|---:|']
    for r in results:
        lines.append(f"| {r['month']} | {r['starting_equity_nzd']:,.2f} | {r['pnl_nzd']:+,.2f} | {r['return_pct']:+.2f}% | {r['ending_equity_nzd']:,.2f} |")
    lines+=['','CSV 额外包含月末现金、持仓股数、最后交易日。复算：`.venv/bin/python monthly_returns.py`。', '',
        '核对通过：72 个月盈亏相加等于期末资产减本金；月收益连乘等于全区间累计收益。']
    (OUT/'monthly_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines[:17]))
    print('2025 年最近完整年度：')
    for r in results[-12:]: print(r['month'],round(r['pnl_nzd'],2),round(r['return_pct'],2),round(r['ending_equity_nzd'],2))

if __name__=='__main__':main()
