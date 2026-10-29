"""Publish a verified initial HK simulator risk snapshot, without binding it.

This initial-flat path deliberately rejects journals containing any trade history.
A running portfolio requires full order/fill reconciliation, not this initializer.
Caller must hold the cross-market admission lock throughout query and publication.
"""
import json
import os
import tempfile
from pathlib import Path
from .hk_account import validate_flat_paper_snapshot
from .hk_policy import amount
from .policy import SafetyError


def publish_initial_flat(path, *, stock, hk_account, accounts, snapshot, journal, now):
    if (stock.get('env') != 'SIMULATE' or not stock.get('account')
        or not hk_account or str(stock['account']) == str(hk_account)
        or not stock.get('portfolio_id')
        or stock.get('hk_account') not in (None, hk_account)
        or amount(stock.get('initial')) != 500
        or amount(stock.get('loss_limit')) != 100):
        raise SafetyError('Explicit shared simulation identity and limits required')
    validate_flat_paper_snapshot(accounts, snapshot, account=hk_account, now=now)
    # Keep the journal write-locked until the snapshot is atomically visible.
    journal.db.execute('BEGIN IMMEDIATE')
    try:
        identity=journal.db.execute('SELECT portfolio FROM identity').fetchone()
        if not identity or identity[0] != stock['portfolio_id']:
            raise SafetyError('Risk snapshot portfolio mismatch')
        for table in ('holds','settled_fills','closed_positions','closing_orders','reservation_intents'):
            if journal.db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise SafetyError('Initial risk snapshot cannot reset trading history')
        row=dict(as_of=snapshot['checked_at'],published_at=now.isoformat(),
                 account=hk_account,us_account=stock['account'],
                 portfolio_id=stock['portfolio_id'],env='SIMULATE',currency='USD',
                 budget_usd=stock['initial'],loss_limit_usd=stock['loss_limit'],
                 reconciled=True,occupied_usd='0',realized_net_usd='0',
                 unrealized_net_usd='0',pending_or_unknown=False,
                 # Initial verification is never sufficient to allow buys.
                 entry_block=True,orders_enabled=False,phase='initial_flat_verified')
        target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=target.name+'.',dir=str(target.parent))
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(row,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        journal.db.commit()
        return row
    except Exception:
        journal.db.rollback()
        raise
