"""Build an explicit paused migration from the unused post-reset acceptance baseline."""
from copy import deepcopy
from datetime import datetime
from .policy import SafetyError
from .capital import config_for_state
from .hk_fx import validate_reference


def targets(stock,option,hk,observer,*,account,hk_account,history,fx_reference,now,migration_id):
    if (account!='000001' or hk_account!='000002' or not migration_id or now.tzinfo is None
            or stock.get('account')!=account or stock.get('env')!='SIMULATE'
            or stock.get('purpose')!='strategy_simulation' or stock.get('strategy_version')!='swing_1_5_v1'
            or not stock.get('portfolio_id') or not stock.get('acceptance_reset_id')
            or stock.get('capital_mode') is not None or stock.get('initial')!=500
            or stock.get('cash')!=500 or stock.get('reserve_usd')!=390
            or stock.get('loss_limit')!=100 or stock.get('paused') is not True
            or stock.get('enabled_stocks') or stock.get('enabled_options')):
        raise SafetyError('Verified paused post-reset US baseline required')
    if (stock.get('orders') or any(p['qty'] for p in stock.get('positions',{}).values())
            or stock.get('queue') or any(stock.get('baseline',{}).values()) or option is not None or hk is not None):
        raise SafetyError('Existing execution activity cannot be reset by deployment')
    if (observer.get('env')!='QUOTE_PAPER' or observer.get('portfolio_id')!=stock['portfolio_id']
            or observer.get('position') or observer.get('trades') or observer.get('cash')!=observer.get('initial')
            or observer.get('paused') is not True):
        raise SafetyError('Idle paused quote-only observer required')
    if (history.get('account')!=hk_account or not history.get('directory') or not history.get('raw_sha256')
            or not history.get('history_start')):raise SafetyError('Verified HK history evidence required')
    datetime.fromisoformat(history['history_start'])
    rate=validate_reference(fx_reference,now)
    stamp=now.isoformat();portfolio=stock['portfolio_id']
    us=deepcopy(stock)
    us.update(capital_mode='independent_sleeves_v1',sleeve='US_STOCK',shared_capital=False,hk_account=hk_account,
        hk_history_start=history['history_start'],hk_empty_history={k:history[k] for k in ('directory','raw_sha256')},
        capital_migration_id=migration_id,independent_execution_deployed_at=stamp)
    options=dict(account=account,env='SIMULATE',fx=stock['fx'],initial=500,cash=500,loss_limit=100,
        budget_currency='USD',option_budget_usd=500,bound_at=stamp,baseline={},positions={},orders={},processed=[],
        paused=True,stopped=stock.get('stopped',False),alerts=[],marks=[],enabled_stocks=False,enabled_options=False,
        option_validation=False,purpose='option_transport_probe',probe_phase='cancel_test',
        portfolio_id=portfolio,capital_mode='independent_sleeves_v1',sleeve='US_OPTION',shared_capital=False,
        acceptance_reset_id=stock['acceptance_reset_id'],capital_migration_id=migration_id)
    hongkong=dict(account=hk_account,env='SIMULATE',purpose='hk_execution',portfolio_id=portfolio,
        capital_mode='independent_sleeves_v1',sleeve='HK',initial=500,budget_currency='USD',loss_limit=100,
        paused=True,stopped=stock.get('stopped',False),bound_at=stamp,capital_migration_id=migration_id,
        accounting_source='hk_reservations.sqlite plus reconciled hk_risk_snapshot.json',live_enabled=False)
    research=deepcopy(observer);research.update(shared_capital=False,execution_budget_usd=0,
        funding_role='quote_only_not_funded',capital_migration_id=migration_id)
    binding=dict(kind='fixed_simulation_accounting',env='SIMULATE',portfolio_id=portfolio,account=hk_account,
        bound_at=stamp,reference=deepcopy(fx_reference),hkd_per_usd=str(rate))
    for state in (us,options):config_for_state(state).validate()
    return dict(US_STOCK=us,US_OPTION=options,HK=hongkong,OBSERVER=research,fx=binding)
