"""Joint-account option transport acceptance; real SIMULATE replies only."""
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError,quote_price
from .simulation_reconcile import account_session,cancel_expired_locked
from .independent_execution import submit_entry
from .desktop_server import read_json


def run_once(root,broker,paths,*,account,hk_account,portfolio,archive,refresh_hk):
    from .option_transport import (fresh_offer,submit_confirmed_option_exit,
                                   verify_independent_evidence)
    root=Path(root);base=root/'data/trading'
    cancel_expired_locked(root,broker,paths,account=account,archive=archive,
                          honor_controls=True,honor_news=True)
    entry=None;complete=False
    with account_session(root,broker,paths,account=account,archive=archive,
                         option_exit_owner='US_OPTION') as (_,evidence,engines):
        engine=engines['US_OPTION'];state=engine.store.load();now=datetime.now(timezone.utc)
        if state.get('purpose')!='option_transport_probe':raise SafetyError('Explicit option acceptance purpose required')
        controls=read_json(base/'simulation_controls.json')
        if controls.get('paused') is not False or controls.get('reset_pending') is not False:state['paused']=True
        if controls.get('stopped') is not False:state['stopped']=True
        engine.store.save(state,'independent_option_controls')
        if engine.blocked_orders(state):return dict(status='waiting_orders')
        if state['stopped']:return dict(status='stopped')
        phase=state.get('probe_phase');orders=list(state['orders'].values())
        code=state.get('probe_code');owned=state['positions'].get(code,{}).get('qty',0)
        if phase=='cancel_wait':
            if owned:raise SafetyError('Cancellation test filled; preserve inventory and inspect')
            if (len(orders)!=1 or orders[-1]['status']!='CANCELLED_ALL'
                    or not orders[-1].get('cancel_requested')):
                raise SafetyError('Cancellation acknowledgment not verified')
            phase='buy';state['probe_phase']=phase;engine.store.save(state,'independent_option_cancel_verified')
        elif phase=='buy_wait':
            if owned!=1:raise SafetyError('Option buy unfilled; no automatic replacement')
            phase='sell';state['probe_phase']=phase;engine.store.save(state,'independent_option_buy_verified')
        elif phase=='sell_wait':
            if owned:raise SafetyError('Option exit unfilled; no automatic replacement')
            phase='complete';state['probe_phase']=phase;engine.mark(state,now)
            engine.store.save(state,'independent_option_sell_verified')
        if phase=='sell':
            if not broker.execution_session(now,strict=False):return dict(status='waiting_regular_session')
            return submit_confirmed_option_exit(engine,now)
        if phase=='complete':complete=True
        elif phase in ('cancel_test','buy'):
            if state['paused']:return dict(status='paused')
            if not broker.execution_session(now,strict=False):return dict(status='waiting_regular_session')
            if phase=='cancel_test':
                offer=read_json(base/'options_broker_candidate.json')
                if not fresh_offer(offer,now):return dict(status='waiting_qualified_contract')
                contract=offer['contract']
            else:contract=state['probe_contract']
            entry=dict(contract=contract,phase=phase)
        else:raise SafetyError('Unknown independent option phase')
    if complete:
        verify_independent_evidence(root,broker,paths,account=account,portfolio=portfolio,
                                    archive=archive,record_completion=True)
        return dict(status='transport_passed')
    contract=entry['contract'];code=contract['code']
    price=quote_price(broker.book(code),'BUY',datetime.now(timezone.utc),True)
    return submit_entry(root,broker,paths,account=account,hk_account=hk_account,portfolio=portfolio,
        archive=archive,sleeve='US_OPTION',code=code,qty=1,price=price,refresh_hk=refresh_hk,
        contract=contract,probe_phase=entry['phase'])
