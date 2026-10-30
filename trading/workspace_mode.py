"""Desktop connection-mode gate; changes permissions, never trading state or orders."""
import json,sqlite3,math
from pathlib import Path
from datetime import datetime
from .engine import TERMINAL

SIMULATION_JOBS=frozenset(('strategy','automation','hk_execution','option_transport','options','scan','hk_scan','hk_catalog','probe','pause','stop'))

def read_mode(root,default='live'):
    path=Path(root)/'data/desktop/workspace_mode.json'
    if not path.exists():return default
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or value.get('mode') not in ('live','simulation'):
        raise ValueError('Invalid workspace mode; connections remain blocked')
    return value['mode']

def state_blockers(state,label,env):
    if not isinstance(state,dict):raise ValueError('Invalid '+label+' ledger')
    if not state:return []
    if state.get('env')!=env:return [label+': unexpected account environment']
    result=[]
    for position in state.get('positions',{}).values():
        qty=float(position.get('qty',0))
        if not math.isfinite(qty) or qty!=0:result.append(label+': holdings remain');break
    if any(order.get('status') not in TERMINAL for order in state.get('orders',{}).values()):
        result.append(label+': pending or unknown orders remain')
    if any(order.get('booked_qty',0)>0 and order.get('fee_final') is not True for order in state.get('orders',{}).values()):
        result.append(label+': filled-order fees are not final')
    if state.get('queue'):result.append(label+': queued order intent remains')
    return result

def blockers(root,target,running,ledger):
    """Inspect durable records and known processes. Never cancel, clear, or kill."""
    root=Path(root);base=root/'data/trading';result=[]
    try:
        if target=='live':
            result.extend('Simulation worker running: '+key for key in sorted(SIMULATION_JOBS) if running(key))
            for name in ('paper.sqlite','probe.sqlite','option_transport.sqlite','hk_runtime.sqlite'):
                result.extend(state_blockers(ledger(base/name),name,'SIMULATE'))
            gate=base/'option_transport_gate.json'
            if gate.exists() and json.loads(gate.read_text()).get('active'):
                result.append('Option simulation transport gate is active')
            journal=base/'hk_reservations.sqlite'
            if journal.exists():
                with sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                    if db.execute("SELECT 1 FROM holds WHERE state!='RELEASED' LIMIT 1").fetchone():
                        result.append('HK simulation reservations or holdings remain')
            journal=base/'hk_submissions.sqlite'
            if journal.exists():
                with sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                    rows=db.execute('SELECT order_id,claimed_at FROM submissions').fetchall()
                if any(not row[0] for row in rows):result.append('HK submission outcome is unknown')
                if rows:
                    snapshot=json.loads((base/'hk_risk_snapshot.json').read_text())
                    stamp=datetime.fromisoformat(snapshot['as_of'])
                    claims=[datetime.fromisoformat(row[1]) for row in rows]
                    if (snapshot.get('reconciled') is not True or snapshot.get('pending_or_unknown') is not False
                        or float(snapshot.get('occupied_usd',-1))!=0 or stamp.tzinfo is None
                        or any(c.tzinfo is None or c>stamp for c in claims)):
                        result.append('HK submission reconciliation is incomplete')
        elif target=='simulation':
            result.extend('Live worker running: '+key for key in ('live_runner','live_operation','account') if running(key))
            folder=base/'live';control=folder/'controls.json'
            if control.exists():
                state=json.loads(control.read_text())
                if state.get('enabled') and not (state.get('paused') or state.get('stopped')):
                    result.append('Pause or stop the armed live portfolio first')
            for market in ('us','hk'):
                result.extend(state_blockers(ledger(folder/(market+'.sqlite')),market.upper()+' live','REAL'))
        else:raise ValueError('Invalid workspace mode')
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        result.append('Account state cannot be verified; preserve records and reconcile before switching')
    return list(dict.fromkeys(result))
