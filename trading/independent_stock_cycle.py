"""Native swing decisions with separate joint-account exit and entry sessions."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError
from .simulation_reconcile import account_session,cancel_expired_locked
from .simulation_admission import observe_us_loss
from .independent_execution import submit_entry
from .swing_engine import SwingEngine


def run_once(root,broker,paths,*,account,hk_account,portfolio,archive,refresh_hk):
    root=Path(root);base=root/'data/trading'
    def read(name):return json.loads((base/name).read_text())
    # Cancel first, even when HK data, news, scans or entry controls are broken.
    cancel_expired_locked(root,broker,paths,account=account,archive=archive,
                          honor_controls=True,honor_news=True)
    try:
        if refresh_hk().get('status')!='reconciled':raise SafetyError('HK refresh unavailable')
    except Exception:
        # The entry helper rechecks and rejects; owned exits still run.
        pass
    from .paper_strategy import current_scan
    from .news_selection import opportunity_pool,extend_us_scan
    from .swing_model import VERSION
    now=datetime.now(timezone.utc)
    parent=current_scan(root,broker,now);short=read('market/short_scan.json')
    matches=(short.get('version')==VERSION and short.get('date')==parent.get('date')
             and short.get('source_generated_at')==parent.get('generated_at'))
    scan=(dict(short,complete=bool(parent.get('complete') and short.get('complete')),
              entry_verified_codes=[c for c in parent.get('signals',{}) if c not in parent.get('rejected',{})])
          if matches else dict(date=parent.get('date'),version=VERSION,complete=False,signals={},candidates=[]))
    # Missing news cannot suppress already-latched or price/time-based exits.
    try:scan=extend_us_scan(scan,opportunity_pool(read('news/latest.json'),now))
    except (OSError,ValueError,KeyError):pass
    entry=None
    with account_session(root,broker,paths,account=account,archive=archive,
                         stock_exit_owner='US_STOCK') as (_,evidence,engines):
        engine=engines['US_STOCK']
        if not isinstance(engine,SwingEngine):raise SafetyError('Independent stock runner requires swing strategy')
        original_reconcile=engine.reconcile
        def reconcile(state,now):
            result=original_reconcile(state,now)
            # Risk failures block entry decisions, not owned protective exits.
            try:
                states={k:e.store.load() for k,e in engines.items()}
                books={c:broker.book(c) for s in states.values() for c,p in s['positions'].items() if p['qty']}
                risk=observe_us_loss(states=states,books=books,hk_snapshot=read('hk_risk_snapshot.json'),
                    account=account,hk_account=hk_account,portfolio=portfolio,now=datetime.now(timezone.utc),
                    risk_path=base/'simulation_loss_latch.sqlite')
                engine._independent_sizing=(state,dict(combined_loss=risk['combined_loss'],
                    available_usd=risk['available']['US_STOCK']),datetime.fromisoformat(evidence['query_started_at']))
            except (SafetyError,OSError,ValueError,KeyError):pass
            return result
        engine.reconcile=reconcile
        state=engine.store.load();controls=read('simulation_controls.json')
        if controls.get('paused') is not False or controls.get('reset_pending') is not False:state['paused']=True
        if controls.get('stopped') is not False:state['stopped']=True
        engine.store.save(state,'independent_stock_controls')
        if engine.blocked_orders(state):return dict(status='pending_orders')
        engine.tick(scan,datetime.now(timezone.utc))
        state=engine.store.load();item=(state.get('queue') or [None])[0]
        if item and item['side']=='SELL':
            engine.drain(scan,datetime.now(timezone.utc))
            return dict(status='exit_serviced')
        if (item and item['side']=='BUY' and not state['paused'] and not state['stopped']
                and not state.get('data_block') and scan.get('complete')):
            entry=item['code']
    if entry is None:return dict(status='no_entry')
    return submit_entry(root,broker,paths,account=account,hk_account=hk_account,
        portfolio=portfolio,archive=archive,sleeve='US_STOCK',code=entry,qty=None,price=1,
        refresh_hk=refresh_hk,queued=True)
