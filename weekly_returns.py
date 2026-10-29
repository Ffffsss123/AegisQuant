"""Weekly account valuation using US session dates and ISO Monday–Sunday weeks."""
import csv
import json
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/real/nzd5000'

def weekly(rows,initial_usd,rate):
    groups=OrderedDict()
    for row in rows:
        day=date.fromisoformat(row['date'])
        monday=day-timedelta(days=day.weekday())
        groups.setdefault(monday.isoformat(),[]).append(row)
    previous=initial_usd
    result=[]
    for monday,group in groups.items():
        end=float(group[-1]['equity'])
        iso=date.fromisoformat(monday).isocalendar()
        result.append(dict(week=f'{iso[0]}-W{iso[1]:02d}',week_start=monday,
            first_trading_day=group[0]['date'],last_trading_day=group[-1]['date'],sessions=len(group),
            starting_equity_nzd=previous/rate,ending_equity_nzd=end/rate,
            pnl_nzd=(end-previous)/rate,return_pct=(end/previous-1)*100,
            reached_100=(end-previous)/rate>=100,cash_nzd=float(group[-1]['cash'])/rate,
            shares=int(group[-1]['shares'])))
        previous=end
    return result


def main():
    meta=json.loads((OUT/'results.json').read_text())
    rate=meta['rate']
    with (OUT/'5_20_equity.csv').open(encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    result=weekly(rows,meta['initial_nzd']*rate,rate)
    assert abs(sum(r['pnl_nzd'] for r in result)-(result[-1]['ending_equity_nzd']-meta['initial_nzd']))<1e-7
    compound=1
    for r in result: compound*=1+r['return_pct']/100
    assert abs(compound-result[-1]['ending_equity_nzd']/meta['initial_nzd'])<1e-9
    with (OUT/'weekly_5_20.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(result[0]));writer.writeheader();writer.writerows(result)
    wins=sum(r['pnl_nzd']>1e-8 for r in result)
    losses=sum(r['pnl_nzd']<-1e-8 for r in result)
    reached=sum(r['reached_100'] for r in result)
    streak=longest=0
    for r in result:
        streak=streak+1 if r['pnl_nzd']<-1e-8 else 0
        longest=max(longest,streak)
    best=max(result,key=lambda r:r['pnl_nzd']);worst=min(result,key=lambda r:r['pnl_nzd'])
    stats=dict(weeks=len(result),winning_weeks=wins,losing_weeks=losses,flat_weeks=len(result)-wins-losses,
        weeks_at_least_100=reached,target_rate=reached/len(result),
        mean_pnl_nzd=sum(r['pnl_nzd'] for r in result)/len(result),longest_losing_streak=longest,
        best_week=best,worst_week=worst)
    (OUT/'weekly_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
    lines=['# 5,000 纽币：每周收益报表','',
        'SPY 5/20 日均线，真实历史行情 2020-01-02 至 2025-12-31。按美国交易日期的周一至周日分组，以每周最后一个交易日收盘估值。', '',
        '周盈亏 = 本周末权益 − 上周末权益；首周从初始 5,000 NZD 计算。周收益率以周初权益为分母。持仓和资金连续滚动，不强制周末卖出，不提现，包含未实现盈亏。', '',
        f'共 {len(result)} 个周区间，含起止两个不完整周及均线预热空仓周；节假日短周正常计入。',
        f"每周平均盈亏 NZD {stats['mean_pnl_nzd']:+.2f}；盈利 {wins} 周，亏损 {losses} 周，持平 {stats['flat_weeks']} 周。",
        f'每周盈利至少 100 NZD：{reached}/{len(result)} 周（{reached/len(result):.1%}）。',
        f"最好一周（按金额）：{best['week']}，NZD {best['pnl_nzd']:+.2f}，{best['return_pct']:+.2f}%。",
        f"最差一周（按金额）：{worst['week']}，NZD {worst['pnl_nzd']:+.2f}，{worst['return_pct']:+.2f}%。",
        f'最长连续亏损：{longest} 周（持平周中断连续亏损计数）。','',
        f'费用口径：不计税和分红；每边手续费和滑点各 5 基点。固定 1 NZD = {rate} USD，不模拟历史汇率或换汇成本。', '',
        '100 NZD 为固定金额门槛；账户价值随时间改变，不等于每周固定 2%。平均周盈亏是全部周金额的算术平均，不代表稳定周收入。该策略已在同一历史数据上比较过，不是独立样本外验证。','',
        '| 周起始日 | 最后交易日 | 周初资产 NZD | 周盈亏 NZD | 周收益率 | 周末资产 NZD | ≥100 NZD |',
        '|---|---|---:|---:|---:|---:|---|']
    for r in result:
        lines.append(f"| {r['week_start']} | {r['last_trading_day']} | {r['starting_equity_nzd']:,.2f} | {r['pnl_nzd']:+,.2f} | {r['return_pct']:+.2f}% | {r['ending_equity_nzd']:,.2f} | {'是' if r['reached_100'] else '否'} |")
    lines+=['','复算：`.venv/bin/python weekly_returns.py`。核对通过：周盈亏求和与全区间净盈亏一致，周收益连乘与期末权益一致。']
    (OUT/'weekly_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
