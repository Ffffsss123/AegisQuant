"""Reproduce a fixed-parameter SPY price-only validation from saved Yahoo JSON."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from backtest import load_prices, run, write_csv

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data/real/SPY_yahoo_raw.json'
OUT = ROOT / 'data/real/validation'
SOURCE = 'https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1=1577836800&period2=1767225600&interval=1d&events=div%2Csplits'


def benchmark(rows, capital=10000, fee_bps=5, slip_bps=5):
    price = rows[0]['open'] * (1 + slip_bps / 10000)
    qty = int(capital / (price * (1 + fee_bps / 10000)))
    cash = capital - qty * price * (1 + fee_bps / 10000)
    values = [cash + qty * r['close'] for r in rows]
    peak, dd = capital, 0
    for value in values:
        peak = max(peak, value)
        dd = max(dd, 1-value/peak)
    return dict(total_return=values[-1]/capital-1, max_drawdown=dd, final_equity=values[-1])


def main():
    payload = json.loads(RAW.read_text())
    if payload['chart']['error']:
        raise ValueError(payload['chart']['error'])
    result = payload['chart']['result'][0]
    if result['meta']['symbol'] != 'SPY' or result['meta']['currency'] != 'USD':
        raise ValueError('标的或币种不匹配')
    if result.get('events', {}).get('splits'):
        raise ValueError('区间存在拆股，需要先增加企业行动处理')
    quotes = result['indicators']['quote'][0]
    rows = []
    for i, stamp in enumerate(result['timestamp']):
        day = datetime.fromtimestamp(stamp, ZoneInfo('America/New_York')).date().isoformat()
        if not '2020-01-01' <= day < '2026-01-01':
            raise ValueError('日期越界')
        vals = {key: quotes[key][i] for key in ['open','high','low','close','volume']}
        if any(v is None for v in vals.values()):
            raise ValueError('缺失行情：'+day)
        if not vals['low'] <= min(vals['open'],vals['close']) <= max(vals['open'],vals['close']) <= vals['high'] or vals['volume'] < 0:
            raise ValueError('OHLC 或成交量不合法：'+day)
        rows.append(dict(date=day, **vals))
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = ROOT / 'data/real/SPY_2020_2025.csv'
    write_csv(csv_path, rows, ['date','open','high','low','close','volume'])
    clean = load_prices(csv_path)
    cases = [('full', '2020—2025 全区间', clean, 5, 5),
             ('early', '2020—2023 前段', [r for r in clean if r['date']<'2024-01-01'], 5, 5),
             ('later', '2024—2025 后段独立重启', [r for r in clean if r['date']>='2024-01-01'], 5, 5),
             ('cost_stress', '全区间成本加倍', clean, 10, 10)]
    results=[]
    for key, label, sample, fee, slip in cases:
        summary, curve, trades = run(sample, fast=20, slow=60, capital=10000, fee_bps=fee, slip_bps=slip)
        base=benchmark(sample, fee_bps=fee, slip_bps=slip)
        results.append(dict(case=key, label=label, strategy=summary, benchmark=base))
        write_csv(OUT/(key+'_equity.csv'),curve,['date','equity','cash','shares','drawdown'])
        write_csv(OUT/(key+'_trades.csv'),trades,['date','side','qty','price','fee'])
    metadata=dict(source=SOURCE, history_page='https://finance.yahoo.com/quote/SPY/history/',
                  processed_at_utc=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(RAW.read_bytes()).hexdigest(),
                  symbol='SPY', currency='USD', bars=len(clean), start=clean[0]['date'],end=clean[-1]['date'],
                  dividends_in_source=len(result.get('events',{}).get('dividends',{})),
                  price_basis='Yahoo open/close, not adjclose; no splits in interval; dividends excluded', results=results)
    (OUT/'validation.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    lines=['# SPY 真实历史行情验证', '',
           f"数据：Yahoo Finance 的 SPY 日线，{len(clean)} 行，{clean[0]['date']} 至 {clean[-1]['date']}，美元。",
           '', '来源：[Yahoo 历史行情](https://finance.yahoo.com/quote/SPY/history/)。原始响应和 SHA-256 校验值保存在本地，可复算。', '',
           '## 方法', '',
           '固定使用已有 20/60 日均线，不搜索最优参数。初始资金 10,000 美元，只做多，整股买入使用可用现金。前一交易日收盘决定信号，下一交易日开盘执行。常规手续费和滑点分别为每边 5 基点（各 0.05%）。', '',
           '**这是价格收益回测，未计分红，不是总回报。** 使用同一来源的 open/close，未混用 adjclose；区间拆股事件为空。数据源记录了分红，但本版暂未建模。买入持有基准也不计分红、按相同费用买入，期末均按收盘估值，不强制平仓。', '',
           '## 结果', '', '| 区间 | 策略收益 | 策略最大回撤 | 买入持有收益 | 基准最大回撤 | 成交笔数 |', '|---|---:|---:|---:|---:|---:|']
    for item in results:
        s,b=item['strategy'],item['benchmark']
        lines.append(f"| {item['label']} | {s['total_return']:.2%} | {s['max_drawdown']:.2%} | {b['total_return']:.2%} | {b['max_drawdown']:.2%} | {s['orders']} |")
    lines += ['', '## 如何理解', '',
              '各段独立从现金开始，策略前 60 根日线等待均线形成；基准从各段第一天开盘买入。因此两者市场暴露不同，后段结果不是连续账户的分段收益。后段未用于调参，但这不是严格的前瞻样本外试验。', '',
              '已检查日期顺序和唯一性、价格为正、OHLC 范围、成交量、缺失值、标的与币种。没有用第二家行情供应商交叉核对，也未独立核对交易所完整日历。', '',
              '局限：未计分红、税费、最低佣金、现金利息、部分成交与容量限制；只有一个 ETF，不能证明策略适用于其他市场。费用是假设，不是 moomoo 实际收费。该结果验证了历史计算流程，不代表可以直接实盘。', '',
              '复算：在项目目录运行 `.venv/bin/python validate_history.py`。原始数据：`data/real/SPY_yahoo_raw.json`；标准 CSV：`data/real/SPY_2020_2025.csv`。']
    (OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines[:22]))


if __name__=='__main__':
    main()
