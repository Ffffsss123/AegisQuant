"""Position presentation only; never changes orders or strategy controls."""
from math import isfinite
from .engine import TERMINAL

def positive(value):
    try:
        value=float(value)
        return value if isfinite(value) and value>0 else None
    except (TypeError,ValueError):return None

def simulation_details(state,code,position):
    qty=positive(position.get('qty'));mult=positive(position.get('multiplier',1));cost=positive(position.get('cost'))
    average=cost/(qty*mult) if qty and mult and cost else None
    pending=[o for o in state.get('orders',{}).values() if o.get('code')==code and o.get('status') not in TERMINAL]
    if any(o.get('status') in ('UNKNOWN','INTENT') for o in pending):status='订单结果待确认 · 正在对账'
    elif any(o.get('cancel_requested') for o in pending):status='已请求撤单 · 等待确认'
    elif pending:
        side='卖出' if pending[0].get('side')=='SELL' else '买入'
        status=side+('部分成交 · 等待余单' if any(o.get('booked_qty',0)>0 for o in pending) else '订单处理中')
    elif state.get('stopped'):status='持有中 · 模拟交易已停止'
    elif any(i.get('code')==code and i.get('side')=='SELL' for i in state.get('queue',[])):status='卖出待提交 · 等待报价及下单检查'
    elif code in state.get('exit_quote_status',{}):
        reason=state['exit_quote_status'][code]
        status='持有中 · '+('买卖盘价差过大，暂不满足卖出报价条件' if reason=='Spread too wide' else '退出报价待核验')
    elif state.get('paused'):status='持有中 · 暂停新买入，继续退出管理'
    else:status='持有中 · 策略监测中'
    return dict(buy_price=average,buy_price_label='买入均价（不含费用）',position_status=status)

def external_details(position,updated_at):
    valid=position.get('cost_price_valid')
    cost=positive(position.get('cost_price')) if valid is True else None
    return dict(buy_price=cost,buy_price_label='券商持仓成本价',position_status='持有中 · 已有实盘，仅查看',position_updated_at=updated_at)
