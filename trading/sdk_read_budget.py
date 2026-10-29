"""Serialize read-query spacing across the simulator processes sharing OpenD."""
import fcntl,time,os
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def spaced_read(name,*,root=None,interval=3.1):
    # Canonical names cover both the US adapter and explicit HK readers.
    names={'accounts':'accounts','positions':'positions','orders':'orders',
           'history_orders':'history_orders','cash':'cash','funds':'cash','fees':'fees'}
    if name not in names:
        yield
        return
    path=Path(root or Path(__file__).resolve().parents[1])/'data/trading/read_query_spacing'/names[name]
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        handle.seek(0);raw=handle.read()
        try:last=float(raw)
        except ValueError:last=0.
        now=time.time()
        # Clock rollback must not cause an unbounded wait; this is a quota guard,
        # never the freshness clock for broker evidence.
        wait=interval-max(0,now-last) if last<=now+interval else interval
        if wait>0:time.sleep(wait)
        handle.seek(0);handle.truncate();handle.write(str(time.time()));handle.flush();os.fsync(handle.fileno())
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)
