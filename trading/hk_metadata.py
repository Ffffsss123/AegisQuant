"""Join current exchange identity, reviewed share class and broker status."""
from datetime import datetime
from .hk_policy import HK, amount
from .hk_classification import reviewed_class
from .hk_tick import tick_size
from .policy import SafetyError


def execution_metadata(root, catalog, snapshot, checked_at, code, price, now):
    if now.tzinfo is None:
        raise SafetyError('Aware HK metadata clock required')
    day=now.astimezone(HK).date().isoformat()
    stamp=datetime.fromisoformat(checked_at)
    if stamp.tzinfo is None or not 0 <= (now-stamp).total_seconds() <= 30:
        raise SafetyError('Fresh HK status query required')
    if catalog.get('source_date') != day:
        raise SafetyError('Current HK exchange directory required')
    row=catalog['rows'][code]
    classification=reviewed_class(root,code,row,now)
    if (snapshot.get('code') != code or snapshot.get('suspension') is not False
            or str(snapshot.get('update_time',''))[:10] != day):
        raise SafetyError('Current unsuspended HK snapshot required')
    lot=amount(row['lot_size'])
    if lot<=0 or lot!=lot.to_integral_value() or amount(snapshot['lot_size'])!=lot:
        raise SafetyError('HK board lot identity changed')
    # HKEX spreadsheet serializes spread table 01 as numeric text "1".
    table='01' if row.get('spread_table') in ('1','01') else row.get('spread_table')
    step=tick_size(price,table=table,kind=classification['kind'],session=day)
    return dict(code=code,verified=True,suspended=False,kind=classification['kind'],
                date=day,lot_size=int(lot),tick_size=str(step),spread_table=table,
                checked_at=checked_at,classification=classification,orders_enabled=False)
