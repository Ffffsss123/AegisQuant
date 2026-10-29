"""Consumer for reconciled HK exposure. No binding or snapshot is created here."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError,number

ROOT=Path(__file__).resolve().parents[1]

def exposure(stock, now=None, root=None):
    account=stock.get('hk_account')
    if account is None:
        return dict(occupied_usd=0.,realized_net_usd=0.,total_net_pnl_usd=0.,entry_block=False)
    now=now or datetime.now(timezone.utc)
    try:
        row=json.loads((Path(root or ROOT)/'data/trading/hk_risk_snapshot.json').read_text())
        stamp=datetime.fromisoformat(row['as_of'])
        if (not account or not stock.get('account') or stock.get('env')!='SIMULATE'
            or not stock.get('portfolio_id') or account==stock['account']
            or row['env']!='SIMULATE' or row['currency']!='USD'
            or row['account']!=account or row['us_account']!=stock['account']
            or row['portfolio_id']!=stock['portfolio_id']
            or row['reconciled'] is not True or row['budget_usd']!=stock['initial']
            or row['loss_limit_usd']!=stock['loss_limit']
            or stamp.tzinfo is None or now.tzinfo is None
            or not 0<=(now-stamp).total_seconds()<=30):
            raise ValueError()
        fields={k:number(row[k],k) for k in ('occupied_usd','realized_net_usd','unrealized_net_usd')}
        if fields['occupied_usd']<0 or type(row['pending_or_unknown']) is not bool or type(row['entry_block']) is not bool:
            raise ValueError()
        return dict(occupied_usd=fields['occupied_usd'],realized_net_usd=fields['realized_net_usd'],
                    total_net_pnl_usd=fields['realized_net_usd']+fields['unrealized_net_usd'],
                    entry_block=row['pending_or_unknown'] or row['entry_block'])
    except (OSError,KeyError,ValueError,TypeError):
        raise SafetyError('Bound HK exposure missing, stale or unreconciled')


def occupied_slots(stock,now=None,root=None):
    """Read conservative HK stock slots under the caller's shared admission lock."""
    if stock.get('hk_account') is None:return 0
    exposure(stock,now,root)  # Require fresh, matching cross-market reconciliation.
    import sqlite3
    path=Path(root or ROOT)/'data/trading/hk_reservations.sqlite'
    db=None
    try:
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        db.execute('BEGIN')
        identity=db.execute('SELECT portfolio FROM identity WHERE id=1').fetchone()
        if not identity or identity[0]!=stock['portfolio_id']:raise ValueError()
        states=[r[0] for r in db.execute('SELECT state FROM holds')]
        if any(state not in ('HELD','COMMITTED','RELEASED') for state in states):raise ValueError()
        return sum(state!='RELEASED' for state in states)
    except (OSError,sqlite3.Error,ValueError,KeyError):
        raise SafetyError('Verified HK position slots unavailable')
    finally:
        if db is not None:db.close()
