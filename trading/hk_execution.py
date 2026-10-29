"""HK SIMULATE entry/exit coordination over the existing durable transport."""
import json,hashlib,fcntl,time
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError,quote_price
from .hk_policy import execution_session,amount,HK
from .desktop_server import read_json,ledger
from .hk_maintenance import refresh_bound_risk
from .hk_signals import current_signals
from .hk_order_request import entry_inputs
from .hk_metadata import execution_metadata
from .news import assess
from .simulation_history import bound_hk_history_start


def run_once(root,resources,us_broker,*,archive):
    root=Path(root);base=root/'data/trading';market=base/'hk_market'
    reader,transport,reservations,quotes,scan_quotes=(resources[k] for k in ('reader','transport','reservations','quotes','scan_quotes'))
    stock=ledger(base/'paper.sqlite');now=reader.clock()
    report=refresh_bound_risk(root,reader,transport,reservations,books=resources['books'])
    if report['status']!='reconciled':return report
    calendar=read_json(market/'scan_calendar.json')
    if calendar.get('range_end','')<now.astimezone(HK).date().isoformat():
        raise SafetyError('Current official HK calendar coverage required')
    calendar=calendar['rows']
    if not execution_session(calendar,now):return dict(report,execution_status='outside_regular_session')
    controls=read_json(base/'simulation_controls.json')
    if controls.get('stopped') is not False:return dict(report,execution_status='stopped')
    scan=read_json(market/'scan.json');catalog=read_json(market/'catalog.json');news=read_json(base/'news/latest.json')
    try:signals=current_signals(scan,calendar,now)
    except (SafetyError,KeyError,ValueError):signals=dict(signals={},candidates=[])
    inv=report['inventory'];requests={tag:json.loads(body) for tag,body in transport.journal.db.execute('SELECT tag,request FROM submissions')}
    # Inspect actual inventory independently of today's candidate membership.
    for code,qty in inv['positions'].items():
        decision=assess(news,code,reader.clock())
        reason='emergency_news' if decision.get('exit_long') else 'trend_reversal' if signals['signals'].get(code,{}).get('sell') else None
        if not reason:continue
        entries=[tag for tag,request in requests.items() if request['code']==code and request['side']=='BUY'
                 and reservations.db.execute("SELECT 1 FROM holds WHERE key=? AND state='COMMITTED'",(tag,)).fetchone()]
        if len(entries)!=1:raise SafetyError('Unique owned HK entry required')
        key=entries[0];tag='HK-SIM-'+hashlib.sha256((key+':SELL').encode()).hexdigest()[:40]
        if tag in requests:continue  # An unfilled exit is never repriced automatically.
        # Finish slow account/metadata reads before acquiring the source book.
        # The final transport still checks the original account and quote times.
        accounts,snapshot=reader.snapshot(bound_hk_history_start(stock,account=reader.account,now=reader.clock()))
        stamp=reader.clock().isoformat();snap=scan_quotes.snapshot(code)[0]
        book=quotes.book(code);price=quote_price(book,'SELL',reader.clock())
        meta=execution_metadata(market,catalog,snap,stamp,code,price,reader.clock())
        inputs=dict(account=reader.account,accounts=accounts,account_checked_at=snapshot['checked_at'],calendar=calendar,
            code=code,side='SELL',qty=int(amount(qty)),book=book,metadata=meta,tag=tag,position_key=key,held_qty=qty)
        def admit_exit(request):
            from .hk_reconciliation import inventory
            if read_json(base/'simulation_controls.json').get('stopped') is not False:raise SafetyError('HK stop preserved')
            current_accounts,current=reader.snapshot(bound_hk_history_start(stock,account=reader.account,now=reader.clock()))
            verified=inventory(transport.journal,current_accounts,current,reader.clock())
            if verified['pending_or_unknown'] or amount(verified['positions'].get(code,0))!=amount(qty):
                raise SafetyError('HK exit holding changed')
            transport.audit('hk_exit_decision',dict(tag=tag,reason=reason,news=decision))
            return True
        oid=transport.submit(inputs,admit_exit)
        return dict(report,execution_status='exit_submitted',order_id=oid)
    if controls.get('paused') is not False or controls.get('reset_pending') is not False:
        return dict(report,execution_status='paused')
    signals=current_signals(scan,calendar,reader.clock())
    from .news_selection import opportunity_pool
    pool=opportunity_pool(news,reader.clock())
    preferred=[r['code'] for r in pool['opportunities'] if r['market']=='HK' and not r['news_blocked']]
    candidates=[r['code'] for r in signals['candidates']]
    # A fresh news issuer may be outside the ranked pool, but its validated
    # history and current execution metadata are still mandatory.
    for code in reversed(preferred):
        signal=signals['signals'].get(code,{})
        if signal.get('buy') is True and signal.get('liquid') is True:
            if code in candidates:candidates.remove(code)
            candidates.insert(0,code)
    for code in candidates:
        if code in inv['positions']:continue
        decision=assess(news,code,reader.clock())
        if decision['global_block']:return dict(report,execution_status='global_news_block')
        if decision['multiplier']<=0 or decision['exit_long']:continue
        completed=any(amount(q)>0 for (q,) in reservations.db.execute('SELECT qty FROM settled_fills'))
        if controls.get('require_news_opportunity') and not completed and not decision.get('entry_evidence'):continue
        accounts,snapshot=reader.snapshot(bound_hk_history_start(stock,account=reader.account,now=reader.clock()))
        stamp=reader.clock().isoformat();snap=scan_quotes.snapshot(code)[0]
        book=quotes.book(code)
        extended=dict(scan,candidates=[dict(code=c,**signals['signals'][c]) for c in candidates])
        inputs=entry_inputs(root=market,portfolio_id=stock['portfolio_id'],account=reader.account,
            accounts=accounts,account_checked_at=snapshot['checked_at'],calendar=calendar,now=reader.clock(),
            scan=extended,catalog=catalog,snapshot=snap,snapshot_checked_at=stamp,code=code,book=book)
        tag=inputs['tag']
        if tag in requests or transport.journal.db.execute('SELECT 1 FROM aborted_admissions WHERE tag=?',(tag,)).fetchone():continue
        from .hk_independent_admission import reserve_buy
        def admit(request):
            from .hk_reconciliation import inventory
            from .hk_account import validate_paper_cash
            current_accounts,current=reader.snapshot(bound_hk_history_start(stock,account=reader.account,now=reader.clock()))
            checked=inventory(transport.journal,current_accounts,current,reader.clock())
            if checked['pending_or_unknown']:raise SafetyError('HK orders changed before entry')
            cash=validate_paper_cash(current_accounts,current,checked,account=reader.account,now=reader.clock())
            return reserve_buy(root,us_broker,{'US_STOCK':base/'paper.sqlite','US_OPTION':base/'option_transport.sqlite'},request,
                account=stock['account'],hk_account=reader.account,portfolio=stock['portfolio_id'],archive=archive,
                reservations=reservations,hk_cash_cap_hkd=cash['cash_cap_hkd'],audit=transport.audit)
        oid=transport.submit(inputs,admit)
        return dict(report,execution_status='entry_submitted',order_id=oid)
    return dict(report,execution_status='no_qualified_entry')


def main():
    from .hk_maintenance import bound_resources
    from .paper_probe import PaperOnly
    from .simulation_history import load_reset_history
    from .validation import protection_passed
    from .rolling_scan import atomic
    root=Path(__file__).resolve().parents[1];base=root/'data/trading'
    with (base/'hk_execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stock=ledger(base/'paper.sqlite');reset=read_json(base/'reset_status.json')
        if stock.get('capital_mode')!='independent_sleeves_v1':raise SafetyError('Independent budget migration required')
        archive=load_reset_history(reset['archive'],account=stock['account'],request_id=stock['acceptance_reset_id'])
        broker=PaperOnly();broker.shared_read_budget=True
        try:
            with bound_resources(root,stock['hk_account'],details=True) as resources:
                while True:
                    started=time.monotonic()
                    try:
                        if not protection_passed(root):raise SafetyError('Protection validation required')
                        report=run_once(root,resources,broker,archive=archive)
                    except Exception as exc:report=dict(status='needs_attention',reason=str(exc),orders_enabled=False)
                    report.update(checked_at=datetime.now(timezone.utc).isoformat(),background_running=True,
                        env='SIMULATE',interface_acceptance=False,strategy_acceptance=False)
                    atomic(base/'hk_execution.report.json',report)
                    time.sleep(max(0,5-(time.monotonic()-started)))
        finally:broker.close()

if __name__=='__main__':main()
