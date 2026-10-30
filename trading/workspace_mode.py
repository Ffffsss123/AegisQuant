"""Desktop connection-mode gate; changes permissions, never trading state or orders."""
import json,sqlite3,math
from pathlib import Path
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
    """Protect leaving REAL mode only. Never cancel, clear, or kill either environment."""
    root=Path(root);base=root/'data/trading';result=[]
    try:
        if target=='live':
            # Simulation activity never prevents entering the REAL connection path.
            # Existing simulated jobs, orders and reservations remain untouched.
            return []
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
