"""Compose HK candidate checks. Closing-price budgets are estimates, not orders."""
from .hk_classification import reviewed_class
from .hk_history import evaluate
from .hk_fx import validate_reference
from .hk_policy import amount
from .hk_fees import order_upper_bound
from .policy import SafetyError


def screen(root,universe,histories,snapshots,calendar,expected,fx,now,*,held=(),budget_usd=500):
    budget=amount(budget_usd)
    if not 0<budget<=500:raise SafetyError('HK screening cannot enlarge shared budget')
    rate=validate_reference(fx,now)
    result=dict(date=expected,orders_enabled=False,complete=True,universe_count=len(universe),
        candidates=[],signals={},rejected={},errors={},scope='Provided HK directory; only independently reviewed share classes eligible',
        budget_note='One-lot closing-price estimate; actual shared remaining cash checked again at submission')
    # Exit signals survive removal from today's directory or entry filters.
    for code in sorted(set(universe)|set(held)):
        if code in held:
            try:result['signals'][code]=evaluate(histories[code],calendar,expected,fx,now)
            except (SafetyError,KeyError,ValueError,TypeError) as exc:result['errors'][code]='Held exit history: '+str(exc)
        try:classification=reviewed_class(root,code,universe[code],now)
        except (SafetyError,KeyError,ValueError,TypeError,OSError) as exc:
            result['rejected'][code]='Share class unverified: '+str(exc);continue
        try:
            meta=universe[code];snapshot=snapshots[code]
            if snapshot.get('code')!=code or not isinstance(snapshot.get('suspension'),bool):raise SafetyError('Snapshot identity or suspension unavailable')
            if snapshot['suspension'] is True:result['rejected'][code]='Suspended';continue
            lot=amount(meta['lot_size'])
            if lot<=0 or lot!=lot.to_integral_value() or amount(snapshot['lot_size'])!=lot:raise SafetyError('Board lot identity changed')
            if str(snapshot.get('update_time',''))[:10]!=expected:raise SafetyError('Snapshot not from completed session')
            signal=evaluate(histories[code],calendar,expected,fx,now);result['signals'][code]=signal
            if not signal['liquid'] or not signal['buy']:result['rejected'][code]='Liquidity or trend filter';continue
            value=amount(histories[code][-1]['close'])*lot
            cost=(value+2*amount(order_upper_bound(value,lot)['total_hkd']))/rate
            if cost>budget:result['rejected'][code]='One board lot exceeds screening budget';continue
            result['candidates'].append(dict(code=code,**signal,lot_size=int(lot),estimated_roundtrip_usd=str(cost),classification=classification))
        except (SafetyError,KeyError,ValueError,TypeError) as exc:result['errors'][code]=str(exc)
    result['complete']=not result['errors']
    result['candidates'].sort(key=lambda r:(-r['strength'],r['code']))
    return result
