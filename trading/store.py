import json
import sqlite3
import fcntl
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from .policy import SafetyError

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path),timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, body TEXT)')
        self.db.commit()
    @contextmanager
    def lock(self):
        with open(str(self.path)+'.lock','a') as f:
            try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise SafetyError('Another runner holds the state lock')
            try: yield
            finally: fcntl.flock(f,fcntl.LOCK_UN)
    def load(self):
        r=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
        if not r: raise SafetyError('Initialize an explicitly chosen account first')
        return json.loads(r[0])
    def exists(self): return self.db.execute('SELECT 1 FROM state').fetchone() is not None
    def save(self,s,kind='checkpoint',detail=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(s,allow_nan=False),))
            self.db.execute('INSERT INTO events(ts,kind,body) VALUES(?,?,?)',
                (datetime.now(timezone.utc).isoformat(),kind,json.dumps(detail or {},allow_nan=False)))
    def save_if_current(self,expected,replacement,kind,detail=None):
        """Atomic migration write, rejecting a changed source (None means absent).

        Caller still owns admission/ledger locks and validates authorization.
        This supplies no migration policy and does not replace the stage journal.
        """
        canonical=lambda value:json.dumps(value,sort_keys=True,allow_nan=False)
        expected_body=None if expected is None else canonical(expected)
        body=canonical(replacement);event=canonical(detail or {})
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT body FROM state WHERE id=1').fetchone()
            current=None if row is None else canonical(json.loads(row[0]))
            if current!=expected_body:
                raise SafetyError('Migration source changed; preserve current ledger')
            self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(body,))
            self.db.execute('INSERT INTO events(ts,kind,body) VALUES(?,?,?)',
                (datetime.now(timezone.utc).isoformat(),kind,event))

    def event(self,kind,body):
        # Raw broker evidence is separate from the normalized strategy state.
        import math
        def clean(v):
            if isinstance(v,float) and not math.isfinite(v):return None
            if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
            if isinstance(v,(list,tuple)):return [clean(x) for x in v]
            return v
        with self.db:
            self.db.execute('INSERT INTO events(ts,kind,body) VALUES(?,?,?)',
                (datetime.now(timezone.utc).isoformat(),kind,json.dumps(clean(body),allow_nan=False,default=str)))
    def close(self): self.db.close()
