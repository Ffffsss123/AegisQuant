"""Offline, long-only SMA backtest; signals execute next session's open."""
import argparse
import csv
import json
import math
from datetime import date
from pathlib import Path


def load_prices(path):
    with open(path, encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not {'date', 'open', 'close'} <= set(reader.fieldnames or []):
            raise ValueError('CSV 必须包含 date,open,close 列')
        rows = []
        for line, row in enumerate(reader, 2):
            day = date.fromisoformat(row['date']).isoformat()
            op, cl = float(row['open']), float(row['close'])
            if not all(math.isfinite(p) and p > 0 for p in (op, cl)):
                raise ValueError('第 %s 行价格必须为正且有限' % line)
            if rows and day <= rows[-1]['date']:
                raise ValueError('日期必须严格递增，不能重复')
            rows.append(dict(date=day, open=op, close=cl))
        return rows


def run(rows, fast=20, slow=60, capital=10000, fee_bps=5, slip_bps=5, lot=1):
    if not 0 < fast < slow or lot < 1:
        raise ValueError('需要 0 < 短均线 < 长均线，交易单位 >= 1')
    if not all(math.isfinite(x) for x in (capital, fee_bps, slip_bps)) or capital <= 0 or not 0 <= fee_bps < 10000 or not 0 <= slip_bps < 10000:
        raise ValueError('资金必须为正，费率和滑点必须在 0 至 10000 基点之间')
    if len(rows) <= slow:
        raise ValueError('数据长度必须大于长均线周期')
    cash, shares, target, peak = capital, 0, False, capital
    closes, curve, trades = [], [], []
    fee, slip = fee_bps / 10000, slip_bps / 10000
    for row in rows:
        # target was calculated using only the previous session's closing prices.
        if target and shares == 0:
            price = row['open'] * (1 + slip)
            qty = int(cash / (price * (1 + fee)) // lot) * lot
            if qty:
                commission = qty * price * fee
                cash -= qty * price + commission
                shares = qty
                trades.append(dict(date=row['date'], side='BUY', qty=qty, price=price, fee=commission))
        elif not target and shares:
            price = row['open'] * (1 - slip)
            commission = shares * price * fee
            cash += shares * price - commission
            trades.append(dict(date=row['date'], side='SELL', qty=shares, price=price, fee=commission))
            shares = 0
        equity = cash + shares * row['close']
        peak = max(peak, equity)
        curve.append(dict(date=row['date'], equity=equity, cash=cash, shares=shares, drawdown=equity / peak - 1))
        closes.append(row['close'])
        target = len(closes) >= slow and sum(closes[-fast:]) / fast > sum(closes[-slow:]) / slow
    summary = dict(start=rows[0]['date'], end=rows[-1]['date'], bars=len(rows), initial_capital=capital,
                   final_equity=curve[-1]['equity'], total_return=curve[-1]['equity'] / capital - 1,
                   max_drawdown=-min(p['drawdown'] for p in curve), orders=len(trades),
                   total_fees=sum(t['fee'] for t in trades), ending_shares=shares,
                   fast=fast, slow=slow, fee_bps=fee_bps, slip_bps=slip_bps, lot=lot)
    return summary, curve, trades


def write_csv(path, rows, fields):
    with open(path, 'w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description='离线双均线回测：收盘生成信号，下一交易日开盘执行')
    parser.add_argument('csv', type=Path)
    parser.add_argument('--fast', type=int, default=20)
    parser.add_argument('--slow', type=int, default=60)
    parser.add_argument('--capital', type=float, default=10000)
    parser.add_argument('--fee-bps', type=float, default=5)
    parser.add_argument('--slip-bps', type=float, default=5)
    parser.add_argument('--lot', type=int, default=1)
    parser.add_argument('--output', type=Path, default=Path('data/results'))
    args = parser.parse_args()
    try:
        summary, curve, trades = run(load_prices(args.csv), args.fast, args.slow, args.capital, args.fee_bps, args.slip_bps, args.lot)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    summary['source_file'] = str(args.csv.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv(args.output / 'equity.csv', curve, ['date', 'equity', 'cash', 'shares', 'drawdown'])
    write_csv(args.output / 'trades.csv', trades, ['date', 'side', 'qty', 'price', 'fee'])
    report = f'''# 离线回测结果

数据文件：{args.csv.name}（示例文件 SYNTHETIC_DEMO.csv 为人工模拟数据，不是真实行情）

- 区间：{summary['start']} 至 {summary['end']}
- 均线周期：{args.fast} / {args.slow}
- 初始资金：{args.capital:,.2f}（与输入价格同币种）
- 期末权益：{summary['final_equity']:,.2f}
- 区间收益：{summary['total_return']:.2%}
- 最大回撤：{summary['max_drawdown']:.2%}
- 成交笔数：{summary['orders']}
- 期末持仓：{summary['ending_shares']}
- 累计手续费：{summary['total_fees']:.2f}

信号在收盘后生成，下一行交易日开盘成交。只做多，买入时使用可用资金；期末持仓按收盘价估值，不强制卖出。
手续费每边 {args.fee_bps} 基点，滑点每边 {args.slip_bps} 基点，仅为模型参数，不代表券商实际收费。
未模拟税费、最低佣金、流动性、停牌、部分成交及分红现金流。价格需为同一币种和一致复权口径。
结果用于验证软件，不构成未来盈利预测。
'''
    (args.output / 'report.md').write_text(report, encoding='utf-8')
    print(report)
    print('文件已保存到：', args.output.resolve())


if __name__ == '__main__':
    main()
