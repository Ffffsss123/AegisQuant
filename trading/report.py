import csv
from collections import OrderedDict
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from .policy import SafetyError

def weekly_rows(s):
    groups=OrderedDict()
    for mark in sorted(s.get('marks',[]),key=lambda m:datetime.fromisoformat(m['time'])):
        day=datetime.fromisoformat(mark['time']).astimezone(ZoneInfo('America/New_York')).date()
        monday=(day-timedelta(days=day.weekday())).isoformat();groups[monday]=mark
    if not groups:return [],0
    changes=s.get('capital_changes',[])
    initial=changes[0]['old_initial'] if changes else s['initial']
    prev=initial;stock=options=fees=flows_seen=0;rows=[];streak=longest=0
    for week,m in groups.items():
        flows=sum(c['amount_usd'] for c in changes if datetime.fromisoformat(c['time'])<=datetime.fromisoformat(m['time']))
        flow=flows-flows_seen;flows_seen=flows
        pnl=m['equity_usd']-prev-flow
        streak=streak+1 if pnl<-.00001 else 0;longest=max(longest,streak)
        rows.append(dict(week_start=week,as_of=m['time'],equity_usd=m['equity_usd'],
            pnl_usd=pnl,capital_flow_usd=flow,return_pct=pnl/(prev+flow)*100 if prev+flow>0 else 0,
            stock_pnl_usd=m['stock_pnl_usd']-stock,option_pnl_usd=m['option_pnl_usd']-options,
            fees_usd=m['fees_usd']-fees))
        prev=m['equity_usd'];stock=m['stock_pnl_usd'];options=m['option_pnl_usd'];fees=m['fees_usd']
    return rows,longest

def weekly_report(s,path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    rows,longest=weekly_rows(s);initial=s['initial']
    if rows:
        with (path/'weekly.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    lines=['# 策略账户周报','',f"环境：{s['env']}；初始预算 {initial:.2f} USD；按美元原值记账。",'',
        '按美国交易日期周一至周日分组，使用已记录的最后一次估值；运行中断可能缺少周末数据，as_of 为实际估值时间。包含浮动盈亏，预算划拨不计作收益，不自动提现。',
        '不计税和分红；股票/期权贡献已扣费用，费用列仅单独展示，不能重复扣除。待券商确认的费用使用保守预留，后续会修正。',
        f"最长连续亏损 {longest} 个已观察周（缺失周不代表持平）；周收益目标不作为盈利保证。",'',
        '| 周开始 | 周盈亏 USD | 预算划拨 USD | 收益率 | 股票贡献 USD | 期权贡献 USD | 费用 USD | 账户权益 USD |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['week_start']} | {r['pnl_usd']:+.2f} | {r['capital_flow_usd']:+.2f} | {r['return_pct']:+.2f}% | {r['stock_pnl_usd']:+.2f} | {r['option_pnl_usd']:+.2f} | {r['fees_usd']:.2f} | {r['equity_usd']:.2f} |")
    lines+=['','新闻风控最近检查（不是收益归因；完整逐次记录保存在 SQLite events/news_decision）：']
    for code,decision in sorted(s.get('news_status',{}).items()):
        lines.append(f"- {code}：新仓额度系数 {decision['multiplier']}；检查时间 {decision.get('checked_at','')}；"+'；'.join(decision.get('reasons',[])))
    lines+=['','告警：']+['- '+a for a in s['alerts']]
    (path/'weekly.md').write_text('\n'.join(lines),encoding='utf-8')
    return rows
