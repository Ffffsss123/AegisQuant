"""Serialized durable control updates; never acquire ledger/admission locks here."""
import fcntl,json,os,tempfile
from pathlib import Path
from .policy import SafetyError


def update_controls(path, transform):
    """Merge from the latest file under its own lock and preserve unknown fields.

    Transform returns a patch. Caller must release this function's lock before
    taking any ledger lock. Missing controls start empty; corrupt files fail.
    """
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_name(path.name+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        current=json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(current,dict):raise SafetyError('Invalid simulation controls')
        patch=transform(dict(current))
        if not isinstance(patch,dict):raise SafetyError('Invalid simulation control patch')
        updated=dict(current,**patch)
        payload=json.dumps(updated,ensure_ascii=False,allow_nan=False)+'\n'
        fd,name=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w') as handle:
                handle.write(payload);handle.flush();os.fsync(handle.fileno())
            os.replace(name,path)
            directory=os.open(str(path.parent),os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(name):os.unlink(name)
        return updated
