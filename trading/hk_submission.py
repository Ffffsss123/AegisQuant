"""Durable at-most-once submission claims for the future HK simulator runner.

Claim under the shared admission lock after full risk checks and before any SDK
mutation. A claim survives timeouts/restarts; absence in a query never frees it.
This journal does not itself grant trading permission or submit an order.
"""
import json
import sqlite3
from .hk_policy import amount
from .policy import SafetyError

class SubmissionJournal:
    def __init__(self,path,account):
        if not str(account).isdigit() or int(account)<=0:
            raise SafetyError('Explicit HK simulation account required')
        self.account=str(account)
        self.db=sqlite3.connect(str(path),timeout=5,isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK(id=1), account TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO binding VALUES(1,?)',(self.account,))
        if self.db.execute('SELECT account FROM binding').fetchone()[0]!=self.account:
            self.db.close();raise SafetyError('Submission account mismatch')
        self.db.execute('CREATE TABLE IF NOT EXISTS submissions (tag TEXT PRIMARY KEY, request TEXT NOT NULL, claimed_at TEXT NOT NULL, order_id TEXT UNIQUE, evidence TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS aborted_admissions (tag TEXT PRIMARY KEY, request TEXT NOT NULL, evidence TEXT NOT NULL)')
    @classmethod
    def open_existing_readonly(cls,path,account):
        """Inspect an existing bound journal without initializing or claiming funds."""
        from pathlib import Path
        if not str(account).isdigit() or int(account)<=0:
            raise SafetyError('Explicit HK simulation account required')
        obj=cls.__new__(cls);obj.account=str(account)
        obj.db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
        try:
            if obj.db.execute('SELECT account FROM binding WHERE id=1').fetchone()!=(obj.account,):
                raise SafetyError('Submission account mismatch')
            obj.db.execute('SELECT tag,request FROM submissions LIMIT 0')
        except Exception:
            obj.db.close();raise
        return obj

    def close(self):self.db.close()
    def claim(self,request,now):
        if (request.get('env')!='SIMULATE' or request.get('account')!=self.account
            or request.get('market')!='HK' or request.get('currency')!='HKD'
            or request.get('side') not in ('BUY','SELL')
            or request.get('order_type')!='NORMAL' or request.get('time_in_force')!='DAY'
            or not str(request.get('tag','')).startswith('HK-SIM-')
            or len(request['tag'])>64 or not str(request.get('code','')).startswith('HK.')
            or now.tzinfo is None):
            raise SafetyError('Validated HK simulation request required')
        qty,price=amount(request.get('qty')),amount(request.get('price'))
        if qty<=0 or qty!=qty.to_integral_value() or price<=0:
            raise SafetyError('Invalid request quantity or price')
        body=json.dumps(request,sort_keys=True,allow_nan=False)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM aborted_admissions WHERE tag=?',(request['tag'],)).fetchone():
                raise SafetyError('Aborted admission identity cannot be reused')
            if request['side']=='SELL':
                entry=self.db.execute('SELECT request FROM submissions WHERE tag=?',(request.get('position_key'),)).fetchone()
                if not entry:raise SafetyError('Sell entry claim is missing')
                original=json.loads(entry[0])
                if original['side']!='BUY' or original['code']!=request['code'] or qty>amount(original['qty']):
                    raise SafetyError('Sell differs from original entry')
            if self.db.execute('SELECT 1 FROM submissions WHERE tag=?',(request['tag'],)).fetchone():
                raise SafetyError('Submission already claimed; query broker, never resend')
            self.db.execute('INSERT INTO submissions VALUES(?,?,?,NULL,NULL)',(request['tag'],body,now.isoformat()))
            self.db.commit()
        except Exception:self.db.rollback();raise
    def reconcile(self,tag,*,account,return_code,rows,evidence):
        if (str(account)!=self.account or type(return_code) is not int or return_code!=0
            or not isinstance(rows,list) or not isinstance(evidence,str) or not evidence.strip()):
            raise SafetyError('Successful explicit-account order query evidence required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            saved=self.db.execute('SELECT request,order_id FROM submissions WHERE tag=?',(tag,)).fetchone()
            if not saved:raise SafetyError('Unknown submission claim')
            request=json.loads(saved[0]);matches=[r for r in rows if r.get('remark')==tag]
            if len(matches)!=1:raise SafetyError('Submission missing or ambiguous; retain reservation')
            r=matches[0];oid=str(r.get('order_id',''))
            if (not oid or oid=='None' or r.get('code')!=request['code']
                or r.get('trd_side')!=request['side']
                or amount(r.get('qty'))!=amount(request['qty'])
                or amount(r.get('price'))!=amount(request['price'])
                or (saved[1] is not None and saved[1]!=oid)):
                raise SafetyError('Broker order differs from submission claim')
            existing=self.db.execute('SELECT tag FROM submissions WHERE order_id=?',(oid,)).fetchone()
            if existing and existing[0]!=tag:raise SafetyError('Broker order already attributed')
            self.db.execute('UPDATE submissions SET order_id=?,evidence=? WHERE tag=?',(oid,evidence,tag))
            self.db.commit();return oid
        except Exception:self.db.rollback();raise
