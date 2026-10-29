"""Durable monotonic loss latch for the independent simulation portfolio.

Not wired to execution. Initialization requires an explicit portfolio identity;
callers must keep admission closed if the configured database is missing.
"""
import sqlite3
from pathlib import Path
from .policy import SafetyError


def initialize(path, portfolio, *, inherited_stop):
    if not isinstance(inherited_stop, bool):
        raise SafetyError('Explicit inherited loss stop required')
    if not isinstance(portfolio, str) or not portfolio or portfolio != portfolio.strip():
        raise SafetyError('Explicit simulation portfolio required')
    # Exclusive creation: never reset an existing risk history.
    with Path(path).open('xb'):
        pass
    db = sqlite3.connect(str(path))
    try:
        db.execute('PRAGMA synchronous=FULL')
        with db:
            db.execute('CREATE TABLE risk(id INTEGER PRIMARY KEY CHECK(id=1), portfolio TEXT NOT NULL, latched INTEGER NOT NULL CHECK(latched IN (0,1)))')
            db.execute('INSERT INTO risk VALUES(1,?,?)', (portfolio, int(inherited_stop)))
    finally:
        db.close()


def retain_loss_stop(path, portfolio, *, env, triggered):
    """Persist true before returning; false can never clear an earlier stop."""
    if env != 'SIMULATE' or not isinstance(triggered, bool):
        raise SafetyError('Explicit simulation loss result required')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)
    try:
        db.execute('PRAGMA synchronous=FULL')
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT portfolio,latched FROM risk WHERE id=1').fetchone()
            if not row or row[0] != portfolio or row[1] not in (0,1):
                raise SafetyError('Loss latch portfolio mismatch or corrupt state')
            latched = bool(row[1]) or triggered
            db.execute('UPDATE risk SET latched=? WHERE id=1', (int(latched),))
        return latched
    finally:
        db.close()


def persistent_budget_view(path, portfolio, sleeves, *, env):
    """Combine verified sleeve arithmetic with the durable stop before returning.

    Caller holds admission lock and supplies fresh reconciled sleeve values.
    This does not initialize a missing risk database or authorize an order.
    """
    from .simulation_budget import budget_view
    candidate = budget_view(sleeves, env=env, loss_latched=False)
    latched = retain_loss_stop(path, portfolio, env=env,
                               triggered=candidate['entry_block'])
    return budget_view(sleeves, env=env, loss_latched=latched)
