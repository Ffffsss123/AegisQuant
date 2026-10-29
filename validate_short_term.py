"""Exploratory short-horizon daily SMA comparison, without parameter selection."""
import json
from pathlib import Path
from backtest import load_prices, run, write_csv
from validate_history import benchmark

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'data/real/short_term'

def main():
    rows=load_prices(ROOT/'data/real/SPY_2020_2025.csv')
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]
    for label,sample in [('2020—2025',rows),('2024—2025',[r for r in rows if r['date']>='2024-01-01'])]:
        for fast,slow in [(5,10),(5,20),(10,20),(20,60)]:
            s,curve,trades=run(sample,fast=fast,slow=slow)
            idx={r['date']:i for i,r in enumerate(sample)}
            entry=None
            durations=[]
            wins=[]
            for t in trades:
                if t['side']=='BUY':
                    entry=t
                elif entry is not None:
                    durations.append(idx[t['date']]-idx[entry['date']])
                    wins.append(t['qty']*t['price']-t['fee']-(entry['qty']*entry['price']+entry['fee'])>0)
                    entry=None
            stress,_,_=run(sample,fast=fast,slow=slow,fee_bps=10,slip_bps=10)
            item=dict(period=label,fast=fast,slow=slow,summary=s,closed_trades=len(wins),
                      win_rate=sum(wins)/len(wins) if wins else None,
                      mean_holding_sessions=sum(durations)/len(durations) if durations else None,
                      doubled_cost_return=stress['total_return'])
            results.append(item)
            key=f'{sample[0]["date"]}_{fast}_{slow}'
            write_csv(OUT/(key+'_trades.csv'),trades,['date','side','qty','price','fee'])
            write_csv(OUT/(key+'_equity.csv'),curve,['date','equity','cash','shares','drawdown'])
    (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    lines=['# SPY 短线日线策略对比','','真实行情：Yahoo Finance SPY，2020-01-02 至 2025-12-31。原始数据沿用 data/real/SPY_yahoo_raw.json。',
    '这是日线上的短周期均线研究，不是分钟级日内交易。所有结果为累计价格收益，未计分红。', '',
    '初始资金 10,000 美元，每边手续费 5 基点、滑点 5 基点；成本加倍为各 10 基点。只做多，整股使用可用现金，前日收盘信号、次日开盘执行。期末未平仓持仓按收盘估值。','',
    '| 区间 | 均线 | 累计收益 | 最大回撤 | 完成交易数 | 胜率 | 平均持仓交易日 | 成本加倍收益 |',
    '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in results:
        s=r['summary']
        lines.append(f"| {r['period']} | {r['fast']}/{r['slow']} | {s['total_return']:.2%} | {s['max_drawdown']:.2%} | {r['closed_trades']} | {r['win_rate']:.1%} | {r['mean_holding_sessions']:.1f} | {r['doubled_cost_return']:.2%} |")
    lines+=['','胜率与平均持仓时间仅统计完成的买卖配对，胜负按扣除费用后的盈亏判断；期末未平仓收益计入累计收益，但不计入胜率。', '',
    '各区间独立从现金开始，均线各自等待慢线周期形成，市场暴露不同。2024—2025 区间已在前一轮研究中看过，因此不能当作全新样本外验证。', '',
    '这是预先列出几组参数后的探索性比较，不能把其中收益最高的一组视为未来最优策略。未计分红、最低佣金、税费、现金利息和成交限制。不能将结果外推到其他标的或分钟线。','']
    for label,sample in [('2020—2025',rows),('2024—2025',[r for r in rows if r['date']>='2024-01-01'])]:
        b=benchmark(sample)
        lines.append(f"买入持有基准（{label}）：累计价格收益 {b['total_return']:.2%}，最大回撤 {b['max_drawdown']:.2%}。")
    lines+=['','复算：`.venv/bin/python validate_short_term.py`。']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines))

if __name__=='__main__':
    main()
