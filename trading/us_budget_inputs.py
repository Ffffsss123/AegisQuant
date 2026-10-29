"""Fresh bid-based USD sleeve arithmetic; caller holds locks and reconciles first."""
from .policy import SafetyError, number, quote_price
from .engine import TERMINAL


def budget_inputs(state, books, *, account, kind, now, for_loss_observation=False):
    if (kind not in ('STOCK','OPTION') or state.get('env')!='SIMULATE'
        or str(state.get('account'))!=account or not account
        or state.get('initial')!=500 or state.get('budget_currency')!='USD'
        or not isinstance(state.get('positions'),dict) or not isinstance(state.get('orders'),dict)):
        raise SafetyError('Explicit independent USD simulation sleeve required')
    if not for_loss_observation and any(o.get('status') not in TERMINAL or o.get('fee_final') is not True for o in state['orders'].values()):
        raise SafetyError('Unresolved sleeve orders or fees')
    def numeric(value):
        if isinstance(value,bool):raise SafetyError('Boolean sleeve amount')
        return number(value)
    pending_fees=reserved=0.
    if for_loss_observation:
        for order in state['orders'].values():
            if order.get('status') in TERMINAL and order.get('fee_final') is True:continue
            if (order.get('status') not in ('SUBMITTED','FILLED_PART') or not order.get('id')
                    or order.get('kind')!=kind or order.get('side') not in ('BUY','SELL')
                    or order.get('multiplier')!=(100 if kind=='OPTION' else 1)):
                raise SafetyError('Unresolved order cannot be valued for loss observation')
            qty,filled,price,reserve,booked=(numeric(order.get(k)) for k in
                ('qty','booked_qty','price','fee_reserve','booked_fee'))
            if not 0<=filled<=qty or qty<=0 or qty!=int(qty) or filled!=int(filled) or price<=0 or min(reserve,booked)<0:
                raise SafetyError('Invalid reconciled pending order amounts')
            fee=max(0,reserve-booked)
            pending_fees+=fee
            if order['side']=='BUY':reserved+=(qty-filled)*price*order['multiplier']+fee
    cash=numeric(state.get('cash'));deployed=value=fees=0.
    if cash<0:raise SafetyError('Negative sleeve cash')
    for code,p in state['positions'].items():
        qty=numeric(p.get('qty'))
        if qty<0 or qty!=int(qty):raise SafetyError('Invalid whole sleeve inventory')
        if not qty:continue
        multiplier=100 if kind=='OPTION' else 1
        if p.get('kind')!=kind or p.get('multiplier')!=multiplier or not code.startswith('US.'):
            raise SafetyError('Mixed or invalid sleeve inventory')
        cost=numeric(p.get('cost'))
        if cost<0:raise SafetyError('Invalid sleeve cost')
        bid=quote_price(books[code],'SELL',now,kind=='OPTION',check_spread=kind=='OPTION')
        deployed+=cost;value+=qty*multiplier*bid;fees+=5 if kind=='OPTION' else 3
    return dict(initial=500,cash=cash,equity=cash+value-fees-pending_fees,deployed=deployed,reserved=reserved,exit_fees=fees)
