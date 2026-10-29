"""HK simulator transport. Not enabled by a runtime or account binding yet.

The caller supplies a full shared-budget/news/reconciliation admission gate. It
runs under the cross-market lock; only literal True permits submission. Neither
this adapter nor a successful SDK response is strategy acceptance evidence.
"""
from datetime import datetime,timezone
from copy import deepcopy
from pathlib import Path
import json
from .hk_order_request import prepare
from .hk_account import _account_and_time
from .shared_capital import admission_lock
from .policy import SafetyError

class HKTransport:
    def __init__(self,sdk,context,journal,root,audit,clock=None):
        if not callable(audit):raise SafetyError('Durable broker audit required')
        self.m=sdk;self.t=context;self.journal=journal;self.root=root;self.audit=audit
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.journal.db.execute('CREATE TABLE IF NOT EXISTS cancels (tag TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)')
    def _call(self,name,fn,**kw):
        self.audit('hk_'+name+'_intent',dict(account=self.journal.account,env='SIMULATE',request=kw))
        try:
            if getattr(self,'shared_read_budget',False):
                from .sdk_read_budget import spaced_read
                with spaced_read(name):ret,data=fn(**kw)
            else:ret,data=fn(**kw)
        except Exception as exc:
            self.audit('hk_'+name+'_uncertain',dict(error_type=type(exc).__name__))
            raise
        rows=data.to_dict('records') if hasattr(data,'to_dict') else data
        self.audit('hk_'+name,dict(account=self.journal.account,env='SIMULATE',return_code=ret,response=rows))
        if ret!=self.m.RET_OK or not isinstance(rows,list):raise SafetyError('HK broker response failed: '+name)
        return rows
    def _kw(self):return dict(acc_id=int(self.journal.account),trd_env=self.m.TrdEnv.SIMULATE)
    def submit(self,inputs,admit):
        if not callable(admit):raise SafetyError('Shared admission gate required')
        with admission_lock(self.root):
            inputs=deepcopy(inputs)
            args=dict(inputs,now=self.clock());request=prepare(**args)
            if request['account']!=self.journal.account:raise SafetyError('Transport account mismatch')
            if self.journal.db.execute('SELECT 1 FROM submissions WHERE tag=?',(request['tag'],)).fetchone():
                raise SafetyError('Submission already claimed; query broker, never repeat admission')
            if self.journal.db.execute('SELECT 1 FROM aborted_admissions WHERE tag=?',(request['tag'],)).fetchone():
                raise SafetyError('Aborted admission identity cannot be reused')
            # Gate must durably reserve cash and verify news, controls and all markets.
            admitted=deepcopy(request)
            def migration_gate():
                if request['side']=='BUY':
                    from .capital import require_no_pending_migration
                    controls=json.loads((Path(self.root)/'data/trading/simulation_controls.json').read_text())
                    require_no_pending_migration(controls)
                    if (controls.get('paused') is not False or controls.get('stopped') is not False
                            or controls.get('reset_pending') is not False):
                        raise SafetyError('HK entry controls unavailable or paused')
            try:
                migration_gate()
                if admit(admitted) is not True:raise SafetyError('HK shared admission denied')
                if admitted!=request:raise SafetyError('Admission changed the validated request')
                # Risk queries may take time. Recheck source quotes and account age.
                request=prepare(**dict(inputs,now=self.clock()))
                migration_gate()
                controls=json.loads((Path(self.root)/'data/trading/simulation_controls.json').read_text())
                if controls.get('stopped') is not False:raise SafetyError('Stop control blocks HK transport')
            except Exception as exc:
                # This block precedes claim and every possible broker mutation.
                # Persist proof before a separate recovery can release a hold.
                self.journal.db.execute('INSERT INTO aborted_admissions VALUES(?,?,?)',
                    (request['tag'],json.dumps(request,sort_keys=True),
                     json.dumps(dict(at=self.clock().isoformat(),error_type=type(exc).__name__,broker_call_started=False))))
                raise
            self.journal.claim(request,self.clock())
            rows=self._call('place',self.t.place_order,code=request['code'],
                trd_side=getattr(self.m.TrdSide,request['side']),qty=request['qty'],price=request['price'],
                order_type=self.m.OrderType.NORMAL,time_in_force=self.m.TimeInForce.DAY,
                adjust_limit=0,remark=request['tag'],fill_outside_rth=False,**self._kw())
            # Only a matching full response resolves the claim. Never retry an ambiguous ACK.
            return self.journal.reconcile(request['tag'],account=self.journal.account,
                return_code=self.m.RET_OK,rows=rows,evidence='audited hk_place response')
    def cancel(self,tag):
        with admission_lock(self.root):
            row=self.journal.db.execute('SELECT claimed_at,order_id FROM submissions WHERE tag=?',(tag,)).fetchone()
            if not row or row[1] is None:raise SafetyError('Reconciled strategy order required for cancel')
            if (self.clock()-datetime.fromisoformat(row[0])).total_seconds()<60:
                raise SafetyError('Order cancellation deadline not reached')
            started=self.clock().isoformat()
            accounts=self._call('accounts',self.t.get_acc_list)
            _account_and_time(accounts,self.journal.account,started,self.clock())
            rows=self._call('orders',self.t.order_list_query,refresh_cache=True,**self._kw())
            oid=self.journal.reconcile(tag,account=self.journal.account,return_code=self.m.RET_OK,
                rows=rows,evidence='audited hk_orders before cancellation')
            from .engine import TERMINAL
            match=[r for r in rows if str(r.get('order_id'))==oid]
            if len(match)!=1 or not match[0].get('order_status'):
                raise SafetyError('Explicit order status required')
            if match[0]['order_status'] in TERMINAL:return 'already_terminal'
            try:
                self.journal.db.execute('INSERT INTO cancels VALUES(?,?)',(tag,self.clock().isoformat()))
            except Exception as exc:
                raise SafetyError('Cancellation already claimed; query before any recovery') from exc
            self._call('cancel',self.t.modify_order,modify_order_op=self.m.ModifyOrderOp.CANCEL,
                       order_id=oid,qty=0,price=0,**self._kw())
            # Response is a request acknowledgment, not proof of cancellation/freed capital.
            return 'cancel_requested'
