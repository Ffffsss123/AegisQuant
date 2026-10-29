"""Lock-held SIMULATE US entry transport; no budget initialization or pause reset."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError
from .simulation_reconcile import account_session
from .simulation_admission import check_us_entry


def submit_entry(root,broker,paths,*,account,hk_account,portfolio,archive,
                 sleeve,code,qty,price,refresh_hk,contract=None,queued=False,probe_phase=None):
    root=Path(root);base=root/'data/trading'
    if sleeve not in ('US_STOCK','US_OPTION') or set(paths)!={'US_STOCK','US_OPTION'}:
        raise SafetyError('Explicit independent execution sleeves required')
    if not callable(refresh_hk) or refresh_hk().get('status')!='reconciled':
        raise SafetyError('Fresh reconciled HK risk required')
    kind='STOCK' if sleeve=='US_STOCK' else 'OPTION'
    def read(name):return json.loads((base/name).read_text())
    def admission(engines,evidence,state,order):
        now=datetime.now(timezone.utc)
        if not 0<=(now-datetime.fromisoformat(evidence['query_started_at'])).total_seconds()<=30:
            raise SafetyError('Joint account evidence expired')
        states={owner:e.store.load() for owner,e in engines.items()}
        books={c:broker.book(c) for s in states.values() for c,p in s['positions'].items() if p['qty']}
        required=order['qty']*order['price']*(100 if kind=='OPTION' else 1)+(10 if kind=='OPTION' else 6)
        result=check_us_entry(states=states,books=books,hk_snapshot=read('hk_risk_snapshot.json'),
            hk_reservations_path=base/'hk_reservations.sqlite',account=account,hk_account=hk_account,
            portfolio=portfolio,now=datetime.now(timezone.utc),risk_path=base/'simulation_loss_latch.sqlite',
            controls=read('simulation_controls.json'),sleeve=sleeve,required_usd=required)
        engines[sleeve].store.event('independent_entry_admission',dict(code=code,required_usd=required,result=json.loads(json.dumps(result,default=str))))
        return result
    def validator(states,engines,evidence,state,order):
        admission(engines,evidence,state,order)
        return True
    with account_session(root,broker,paths,account=account,archive=archive,
                         entry_owner=sleeve,entry_validator=validator) as (states,evidence,engines):
        engine=engines[sleeve];state=engine.store.load()
        from .swing_engine import SwingEngine
        if kind=='STOCK' and (not isinstance(engine,SwingEngine) or state.get('purpose')!='strategy_simulation'):
            raise SafetyError('Explicit stock swing strategy required')
        if qty is None and kind!='STOCK':raise SafetyError('Explicit option quantity required')
        initial=admission(engines,evidence,state,dict(qty=qty or 0,price=price))
        parent=read('market/scan.json');session=broker.execution_session(datetime.now(timezone.utc),strict=False)
        if not session or parent.get('complete') is not True or parent.get('date')!=session[1]:
            raise SafetyError('Complete current US scan required')
        if kind=='OPTION':
            from .option_transport import validate_contract
            if not contract or contract.get('code')!=code:raise SafetyError('Explicit entry contract required')
            contract=validate_contract(broker,contract,datetime.now(timezone.utc),scan=parent,available_cash=initial['available_usd'])
        else:
            from .swing_model import VERSION
            from .news_selection import opportunity_pool,extend_us_scan
            scan=read('market/short_scan.json')
            if (scan.get('version')!=VERSION or not scan.get('complete') or scan.get('date')!=parent['date']
                    or scan.get('source_generated_at')!=parent.get('generated_at')):
                raise SafetyError('Complete matching swing signals required')
            scan=dict(scan,entry_verified_codes=[c for c in parent.get('signals',{}) if c not in parent.get('rejected',{})])
            scan=extend_us_scan(scan,opportunity_pool(read('news/latest.json'),datetime.now(timezone.utc)))
            if code not in {r['code'] for r in scan['candidates']}:raise SafetyError('Stock strategy entry signal missing')
            engine.active_scan=scan
        def sizing(state,order):
            result=admission(engines,evidence,state,order)
            engine._independent_sizing=(state,result,datetime.fromisoformat(evidence['query_started_at']))
        engine.independent_entry_budget=sizing
        if qty is None:
            from .policy import quote_price
            price=quote_price(broker.book(code),'BUY',datetime.now(timezone.utc))
            factor=engine.news_check(state,code,datetime.now(timezone.utc))
            sizing(state,dict(qty=0,price=price))
            qty=engine.order_quantity(state,dict(code=code),price,factor)
            if qty<=0:return dict(status='no_affordable_quantity',acknowledged=False)
        if queued:
            queue=state.get('queue',[])
            session=broker.execution_session(datetime.now(timezone.utc),strict=False)
            if (not session or state.get('queue_day')!=session[0] or not queue
                    or queue[0].get('code')!=code or queue[0].get('side')!='BUY'):
                raise SafetyError('Durable current entry queue changed')
            item=queue.pop(0)
            engine.store.save(state,'independent_queue_claim',item)
        if probe_phase is not None:
            if (kind!='OPTION' or state.get('purpose')!='option_transport_probe'
                    or probe_phase not in ('cancel_test','buy') or state.get('probe_phase')!=probe_phase):
                raise SafetyError('Explicit current option probe entry phase required')
            if state.get('probe_code') not in (None,code):raise SafetyError('Option probe contract changed')
            if probe_phase=='cancel_test':
                import math
                book=broker.book(code);tick=contract['tick_size']
                from .policy import quote_price
                quote_price(book,'BUY',datetime.now(timezone.utc),True)
                price=round(math.floor(book['bid']*.5/tick)*tick,8)
                if not 0<price<book['bid']:raise SafetyError('Invalid cancellation-test limit')
            # Bind only after all entry checks, in the same save as order_intent.
            # Rejections leave no contract pin; uncertain sends retain ownership.
            before_intent=engine.before_order_intent
            def bind_probe_at_intent(current,order,tag):
                before_intent(current,order,tag)
                current.update(probe_contract=contract,probe_code=code,
                    probe_phase='cancel_wait' if probe_phase=='cancel_test' else 'buy_wait')
            engine.before_order_intent=bind_probe_at_intent
            try:
                tag=engine.submit(state,code,'BUY',qty,price,datetime.now(timezone.utc),
                    kind='OPTION',contract=contract,execution_purpose='option_transport_probe')
            finally:engine.before_order_intent=before_intent
        else:
            tag=engine.submit(state,code,'BUY',qty,price,datetime.now(timezone.utc),kind=kind,contract=contract)
        order=engine.store.load()['orders'][tag]
        return dict(tag=tag,status=order['status'],order_id=order['id'],acknowledged=bool(order['id']) and order['status']=='SUBMITTED')
