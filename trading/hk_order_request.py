"""HK simulation request validation; shared admission must precede submission.

This module neither connects to a broker nor authorizes/submits an order.
"""
from .hk_account import _account_and_time
from .hk_policy import execution_session, board_order, amount, HK
from .policy import SafetyError, quote_price


def prepare(*, account, accounts, account_checked_at, calendar, now,
            code, side, qty, book, metadata, tag, held_qty=0, position_key=None):
    bound = _account_and_time(accounts, account, account_checked_at, now)
    if bound.get('acc_type') != 'CASH' or bound.get('sim_acc_type') != 'STOCK':
        raise SafetyError('HK stock cash simulator required')
    if execution_session(calendar, now) is None:
        raise SafetyError('Outside HK continuous trading session')
    if side not in ('BUY', 'SELL'):
        raise SafetyError('Unsupported HK order side')
    if not isinstance(tag, str) or not tag.startswith('HK-SIM-') or len(tag)>64:
        raise SafetyError('HK simulation order identity required')
    if (metadata.get('code') != code or metadata.get('verified') is not True
        or metadata.get('suspended') is not False
        or metadata.get('kind') not in ('COMMON', 'ETF_NORMAL')
        or metadata.get('date') != now.astimezone(HK).date().isoformat()):
        raise SafetyError('Current verified HK instrument metadata required')
    # Use exactly the fresh ask/bid. No market order, chase or implicit short.
    price = quote_price(book, side, now)
    value = board_order(code, qty, price, metadata.get('lot_size'), metadata.get('tick_size'))
    if side=='SELL' and (not isinstance(position_key,str) or not position_key.startswith('HK-SIM-') or position_key==tag):
        raise SafetyError('Explicit HK entry identity required for sell')
    if side == 'SELL' and amount(qty)>amount(held_qty):
        raise SafetyError('HK sell exceeds reconciled strategy holding')
    return dict(account=str(account), env='SIMULATE', market='HK', currency='HKD',
                code=code, side=side, qty=int(amount(qty)), price=price,
                notional_hkd=str(value), tag=tag, position_key=tag if side=='BUY' else position_key, order_type='NORMAL',
                time_in_force='DAY', orders_enabled=False,
                status='request_validated_shared_admission_pending')


def entry_inputs(*, root, portfolio_id, account, accounts, account_checked_at,
                 calendar, now, scan, catalog, snapshot, snapshot_checked_at,
                 code, book):
    """Build one-board-lot candidate request; still requires shared admission.

    Identity is stable across restarts for this portfolio/account/session/symbol.
    A rejected/uncertain attempt cannot be reissued under a fresh random tag.
    """
    import hashlib
    import json
    from .hk_signals import current_signals
    from .hk_metadata import execution_metadata
    if not isinstance(portfolio_id,str) or not portfolio_id:
        raise SafetyError('Bound portfolio identity required')
    signals=current_signals(scan,calendar,now)
    if code not in [r['code'] for r in signals['candidates']]:
        raise SafetyError('HK code is not a verified current candidate')
    price=quote_price(book,'BUY',now)
    metadata=execution_metadata(root,catalog,snapshot,snapshot_checked_at,code,price,now)
    identity=json.dumps([portfolio_id,str(account),signals['session'],code,'BUY'],separators=(',',':'))
    tag='HK-SIM-'+hashlib.sha256(identity.encode()).hexdigest()[:40]
    inputs=dict(account=account,accounts=accounts,account_checked_at=account_checked_at,
                calendar=calendar,code=code,side='BUY',qty=metadata['lot_size'],
                book=book,metadata=metadata,tag=tag)
    prepare(**inputs,now=now)
    return inputs
