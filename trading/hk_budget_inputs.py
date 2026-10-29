"""Translate verified HK USD risk snapshots for independent simulation budgets."""
from datetime import datetime
from .hk_policy import amount
from .policy import SafetyError


def budget_inputs(row, *, account, portfolio, now, for_loss_observation=False):
    try:
        stamp=datetime.fromisoformat(row['as_of'])
        if (not account or not portfolio or row['account']!=account or row['portfolio_id']!=portfolio
            or row['env']!='SIMULATE' or row['currency']!='USD' or row['reconciled'] is not True
            or row['pending_or_unknown'] is not False
            or not isinstance(row['entry_block'], bool)
            or (row['entry_block'] and not for_loss_observation)
            or amount(row['budget_usd'])!=500 or amount(row['loss_limit_usd'])!=100
            or stamp.tzinfo is None or now.tzinfo is None or not 0<=(now-stamp).total_seconds()<=30):
            raise SafetyError('HK independent budget snapshot unavailable')
        cost,held,fees,occupied=(amount(row[k]) for k in ('committed_debit_usd','held_usd','exit_fee_reserve_usd','occupied_usd'))
        if min(cost,held,fees)<0 or occupied!=cost+held+fees or held!=0:
            raise SafetyError('HK risk breakdown inconsistent or unresolved')
        realized,unrealized=amount(row['realized_net_usd']),amount(row['unrealized_net_usd'])
        cash=amount(500)+realized-cost
        if cash<0:raise SafetyError('HK virtual cash negative')
        return dict(initial=amount(500),cash=cash,equity=amount(500)+realized+unrealized,
                    deployed=cost,reserved=held,exit_fees=fees)
    except (KeyError,TypeError,ValueError) as exc:
        raise SafetyError('Incomplete HK independent budget evidence') from exc
