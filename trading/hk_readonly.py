"""Explicit HK simulator reads with durable raw evidence; no order methods."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from .hk_account import _account_and_time, validate_flat_paper_snapshot
from .policy import SafetyError

class HKReadOnly:
    def __init__(self, sdk, context, account, evidence_path, clock=None):
        if not str(account).isdigit() or int(account)<=0:
            raise SafetyError('Explicit numeric HK simulator account required')
        self.sdk=sdk;self.context=context;self.account=str(account)
        self.path=Path(evidence_path);self.clock=clock or (lambda:datetime.now(timezone.utc))
    def query(self, name, fn, **kwargs):
        started=self.clock().isoformat()
        try:
            if getattr(self,'shared_read_budget',False):
                from .sdk_read_budget import spaced_read
                with spaced_read(name):ret,data=fn(**kwargs)
            else:ret,data=fn(**kwargs)
        except Exception as exc:
            self._record(dict(query=name,started_at=started,completed_at=self.clock().isoformat(),
                account=self.account,env='SIMULATE',request=dict(kwargs),return_code=None,response=None,error_type=type(exc).__name__))
            raise
        rows=data.to_dict('records') if hasattr(data,'to_dict') else data
        record=dict(query=name,started_at=started,completed_at=self.clock().isoformat(),
                    account=self.account,env='SIMULATE',request=dict(kwargs),return_code=ret,response=rows)
        self._record(record)
        if ret!=self.sdk.RET_OK or not isinstance(rows,list):
            raise SafetyError('HK read failed: '+name)
        return dict(return_code=ret,response=rows)
    def _record(self,record):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a') as f:
            f.write(json.dumps(record,ensure_ascii=False,default=str)+'\n');f.flush();os.fsync(f.fileno())

    def orders_snapshot(self, history_start=None):
        # Earliest query time is the age bound, never timestamp old data at completion.
        started=self.clock().isoformat()
        accounts=self.query('accounts',self.context.get_acc_list)['response']
        a=_account_and_time(accounts,self.account,started,self.clock())
        if a.get('acc_type')!='CASH' or a.get('sim_acc_type')!='STOCK':
            raise SafetyError('HK cash stock simulator required')
        kw=dict(acc_id=int(self.account),trd_env=self.sdk.TrdEnv.SIMULATE,refresh_cache=True)
        snapshot=dict(checked_at=started,env='SIMULATE',account=self.account,account_suffix=self.account[-4:],
                      read_only=True,orders_submitted=0)
        history=[]
        if history_start is not None:
            from datetime import date
            from .hk_policy import HK
            day=date.fromisoformat(history_start)
            end=self.clock().astimezone(HK).date()
            if not 0<=(end-day).days<=89:raise SafetyError('Supported complete HK order history range required')
            history=self.query('history_orders',self.context.history_order_list_query,
                acc_id=int(self.account),trd_env=self.sdk.TrdEnv.SIMULATE,
                start=day.isoformat(),end=end.isoformat())['response']
        current=self.query('orders',self.context.order_list_query,**kw)
        if history_start is not None:
            for group in (history,current['response']):
                ids=[str(r.get('order_id','')) for r in group]
                if any(v in ('','None') for v in ids) or len(ids)!=len(set(ids)):
                    raise SafetyError('Invalid or duplicate HK order query rows')
            merged={str(r['order_id']):r for r in history}
            for row in current['response']:merged[str(row['order_id'])]=row
            current=dict(return_code=0,response=list(merged.values()))
        snapshot['orders']=current
        _account_and_time(accounts,self.account,started,self.clock())
        return accounts,snapshot

    def complete_snapshot(self,accounts,snapshot):
        # Invoke only AFTER servicing orders from orders_snapshot. A failed or
        # slow funds/position read can no longer skip that earlier cancellation.
        _account_and_time(accounts,self.account,snapshot.get('checked_at'),self.clock())
        if snapshot.get('account')!=self.account or snapshot.get('env')!='SIMULATE':
            raise SafetyError('HK snapshot account mismatch')
        kw=dict(acc_id=int(self.account),trd_env=self.sdk.TrdEnv.SIMULATE,refresh_cache=True)
        completed=dict(snapshot)
        completed['funds']=self.query('funds',self.context.accinfo_query,currency=self.sdk.Currency.HKD,**kw)
        completed['positions']=self.query('positions',self.context.position_list_query,**kw)
        _account_and_time(accounts,self.account,snapshot['checked_at'],self.clock())
        return completed

    def snapshot(self,history_start=None):
        accounts,snapshot=self.orders_snapshot(history_start)
        return accounts,self.complete_snapshot(accounts,snapshot)

    def flat_snapshot(self):
        accounts,snapshot=self.snapshot()
        validate_flat_paper_snapshot(accounts,snapshot,account=self.account,now=self.clock())
        return accounts,snapshot

    def migration_flat_snapshot(self, locked_stock_state):
        """Read the HK leg of initial migration verification under caller locks.

        The caller owns admission/target locks and verifies local HK ledgers and
        the US account separately. This method takes no locks, grants no budget
        and returns evidence rather than a whole-migration success boolean.
        """
        from .simulation_history import bound_hk_history_start
        start=bound_hk_history_start(locked_stock_state,account=self.account,now=self.clock())
        accounts,snapshot=self.snapshot(history_start=start)
        validate_flat_paper_snapshot(accounts,snapshot,account=self.account,now=self.clock())
        return accounts,snapshot
