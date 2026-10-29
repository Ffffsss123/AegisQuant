"""Read-only HK scan composition with injected, audited quote providers."""
from pathlib import Path
from datetime import datetime
from .hk_classification import reviewed_class
from .hk_screen import screen
from .hk_scan_publication import publish
from .hk_policy import HK
from .policy import SafetyError
from .rolling_scan import atomic


def collect(root, catalog, calendar, expected, fx, now, history, snapshot, *, held=()):
    """Providers return raw SDK record lists; caller owns a scanner process lock.

    Never acquires an account/trading context. Classification failures are
    explicit exclusions; acquisition failures prevent complete publication.
    Held symbols are always fetched even when removed from the directory.
    """
    root = Path(root)
    if now.tzinfo is None or catalog.get('source_date') != now.astimezone(HK).date().isoformat():
        raise SafetyError('HK collection requires current directory')
    universe = catalog['rows']
    selected = set(held)
    for code, metadata in universe.items():
        try:
            reviewed_class(root, code, metadata, now)
            selected.add(code)
        except (SafetyError, KeyError, ValueError, TypeError, OSError):
            continue
    progress = dict(date=expected, status='collecting', total=len(selected), completed=0,
                    errors={}, orders_enabled=False, started_at=now.isoformat())
    atomic(root/'scan_progress.json', progress)
    histories, snapshots = {}, {}
    for code in sorted(selected):
        try:
            rows = history(code)
            if not isinstance(rows, list) or not rows or any(r.get('code') != code for r in rows):
                raise SafetyError('HK history identity mismatch or empty')
            # Keep the actual raw records separately; no fabricated source time.
            atomic(root/'scan_raw'/f'{code}.history.json', dict(rows=rows))
            histories[code] = [dict(r, date=str(r['time_key'])[:10]) for r in rows
                               if str(r['time_key'])[:10] <= expected]
            if code in universe:
                rows = snapshot(code)
                if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('code') != code:
                    raise SafetyError('HK snapshot identity mismatch')
                atomic(root/'scan_raw'/f'{code}.snapshot.json', dict(rows=rows))
                snapshots[code] = rows[0]
        except (SafetyError, KeyError, ValueError, TypeError, OSError) as exc:
            progress['errors'][code] = str(exc)
        progress['completed'] += 1
        atomic(root/'scan_progress.json', progress)
    result = screen(root, universe, histories, snapshots, calendar, expected, fx, now, held=held)
    result['errors'].update(progress['errors'])
    result['complete'] = not result['errors']
    result.update(acquired_symbols=len(histories), eligible_symbols=len(selected),
                  directory_source_date=catalog['source_date'])
    if result['complete']:
        publish(root, result, expected, now)
        progress['status'] = 'complete'
    else:
        progress['status'] = 'incomplete'
    progress['errors'] = result['errors']
    atomic(root/'scan_progress.json', progress)
    return result
