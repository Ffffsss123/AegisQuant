"""Read existing sleeve ledgers while holding every cooperating Store lock."""
import fcntl
import json
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from .policy import SafetyError


class SnapshotBusy(SafetyError):
    """Transient lock contention; release admission and retry without clearing controls."""


@contextmanager
def locked_ledgers(paths):
    """Caller acquires admission_lock first; locks stay held through validation.

    Does not initialize databases or replace broker freshness/account checks.
    Nonblocking acquisition avoids waiting on a runner with another lock order.
    """
    if not isinstance(paths, dict) or not paths:
        raise SafetyError('Explicit sleeve paths required')
    resolved = {owner: Path(path).resolve() for owner, path in paths.items()}
    if len(set(resolved.values())) != len(resolved):
        raise SafetyError('Duplicate sleeve database')
    if any(not isinstance(owner, str) or not owner or not path.is_file()
           for owner, path in resolved.items()):
        raise SafetyError('Existing named sleeve databases required')
    with ExitStack() as stack:
        for path in sorted(resolved.values()):
            handle = stack.enter_context(open(str(path) + '.lock', 'a'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SnapshotBusy('Sleeve snapshot busy; retry without admission') from exc
        states = {}
        for owner, path in resolved.items():
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                row = db.execute('SELECT body FROM state WHERE id=1').fetchone()
                if row is None:
                    raise SafetyError('Uninitialized sleeve ledger')
                states[owner] = json.loads(row[0])
            finally:
                db.close()
        yield states
