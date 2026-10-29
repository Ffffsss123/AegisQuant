"""HK simulator maintenance entry point. No candidate placement or live mode.

This does not bind the US strategy to HK. The shared ledger must already have
been bound by a separately validated integration before exposure is published.
"""
import argparse,fcntl,json,time
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError
from .desktop_server import ledger,read_json
from .hk_submission import SubmissionJournal
from .hk_reservations import Reservations
from .hk_readonly import HKReadOnly
from .hk_transport import HKTransport
from .hk_cycle import run_once
from .store import Store
from .rolling_scan import atomic
from .validation import protection_passed
from .shared_capital import admission_lock


def mark_stopped(root,account):
    """A stopped producer must never leave apparently healthy fresh exposure."""
    data=root/'data/trading'
    with admission_lock(root):
        path=data/'hk_risk_snapshot.json'
        risk=read_json(path)
        if risk.get('account')==str(account):path.unlink(missing_ok=True)
        report=read_json(data/'hk_runtime.report.json')
        report.update(background_running=False,orders_enabled=False,
                      stopped_at=datetime.now(timezone.utc).isoformat())
        atomic(data/'hk_runtime.report.json',report)


def strategy(root,account):
    s=ledger(root/'data/trading/paper.sqlite')
    if (s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation'
        or not s.get('portfolio_id') or str(s.get('account'))==str(account)
        or s.get('hk_account') not in (None,str(account))):
        raise SafetyError('Explicit separate simulator identities required')
    return s


def refresh_bound_risk(root,reader,transport,reservations,*,books):
    """Refresh a previously bound portfolio using caller-owned HK resources.

    Call before entering US admission locks. No account binding, initialization,
    resource shutdown or candidate submission is performed here.
    """
    root=Path(root)
    if root.resolve()!=Path(transport.root).resolve():
        raise SafetyError('HK refresh workspace mismatch')
    stock=strategy(root,reader.account)
    if stock.get('hk_account')!=reader.account:
        raise SafetyError('Existing HK shared binding required for risk refresh')
    from .simulation_history import bound_hk_history_start
    try:
        start=bound_hk_history_start(stock,account=reader.account,now=reader.clock())
        return run_once(reader,transport,reservations,stock,
            fx=read_json(root/'data/trading/hk_fx_binding.json'),fee_records=None,
            books=books,history_start=start)
    except Exception:
        # Pre-cycle history/FX failures must invalidate previous success too.
        # Identity/workspace checks above prevent deleting another binding.
        with admission_lock(root):
            (root/'data/trading/hk_risk_snapshot.json').unlink(missing_ok=True)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1];data=root/'data/trading'
    if not args.account.isdigit():raise SafetyError('Explicit numeric simulator account required')
    if not protection_passed(root):raise SafetyError('Protection validation required')
    stock=strategy(root,args.account)
    with (data/'hk_maintenance.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        import moomoo as sdk
        from .quote_feed import quote_context
        from .moomoo import Moomoo
        audit=Store(data/'hk_runtime.sqlite')
        journal=SubmissionJournal(data/'hk_submissions.sqlite',args.account)
        reservations=Reservations(data/'hk_reservations.sqlite',stock['portfolio_id'])
        t=q=None
        try:
            t=sdk.OpenSecTradeContext(filter_trdmarket=sdk.TrdMarket.HK,host='127.0.0.1',port=11111,security_firm=sdk.SecurityFirm.FUTUAU)
            q=quote_context(sdk,host='127.0.0.1',port=11111)
            # Reuse only the quote adapter; no US account or order context.
            class Quotes:
                book=Moomoo.book
                call=Moomoo.call
                def now(self):return datetime.now(timezone.utc)
            quotes=Quotes();quotes.m=sdk;quotes.q=q;quotes._book_subscriptions=set();quotes._last={}
            reader=HKReadOnly(sdk,t,args.account,data/'hk_readonly_evidence.jsonl')
            transport=HKTransport(sdk,t,journal,root,audit.event);transport.shared_read_budget=True
            def books():
                # Include terminal buys whose HELD reservation will settle in
                # this cycle; omit released historical symbols.
                held={r[0] for r in reservations.db.execute("SELECT key FROM holds WHERE state!='RELEASED'")}
                codes={json.loads(body)['code'] for tag,body in journal.db.execute('SELECT tag,request FROM submissions') if tag in held}
                return {code:quotes.book(code) for code in sorted(codes)}
            while True:
                if not protection_passed(root):raise SafetyError('Protection validation changed')
                stock=strategy(root,args.account)
                first=journal.db.execute('SELECT min(claimed_at) FROM submissions').fetchone()[0]
                history_start=first[:10] if first else None
                if stock.get('hk_account')==reader.account:
                    from .simulation_history import bound_hk_history_start
                    history_start=bound_hk_history_start(stock,account=reader.account,now=reader.clock())
                report=run_once(reader,transport,reservations,stock,
                    fx=read_json(data/'hk_fx_binding.json'),fee_records=None,books=books,
                    history_start=history_start)
                report.update(checked_at=datetime.now(timezone.utc).isoformat(),
                    run_mode='maintenance_only',background_running=args.loop,new_orders_submitted=0)
                atomic(data/'hk_runtime.report.json',report)
                audit.event('hk_maintenance_cycle',report)
                print(json.dumps({k:report.get(k) for k in ('status','checked_at','errors')},ensure_ascii=False),flush=True)
                if not args.loop:break
                time.sleep(30)
        finally:
            try:
                mark_stopped(root,args.account)
            finally:
                try:
                    if t:t.close()
                finally:
                    if q:q.close()
                    journal.close();reservations.close();audit.db.close()

if __name__=='__main__':main()


from contextlib import contextmanager

@contextmanager
def bound_resources(root,account,*,details=False):
    """Caller-owned HK maintenance resources, never account/budget initialization."""
    root=Path(root);base=root/'data/trading';stock=strategy(root,account)
    if stock.get('hk_account')!=str(account):raise SafetyError('Existing HK binding required')
    if not (base/'hk_runtime.sqlite').exists() or not (base/'hk_submissions.sqlite').exists() or not (base/'hk_reservations.sqlite').exists():
        raise SafetyError('Existing HK runtime and journals required')
    import moomoo as sdk
    from .quote_feed import quote_context
    from .moomoo import Moomoo
    audit=Store(base/'hk_runtime.sqlite');journal=None;reservations=None;t=q=None
    try:
        journal=SubmissionJournal(base/'hk_submissions.sqlite',str(account))
        reservations=Reservations(base/'hk_reservations.sqlite',stock['portfolio_id'])
        t=sdk.OpenSecTradeContext(filter_trdmarket=sdk.TrdMarket.HK,host='127.0.0.1',port=11111,security_firm=sdk.SecurityFirm.FUTUAU)
        q=quote_context(sdk,host='127.0.0.1',port=11111)
        class Quotes:
            book=Moomoo.book
            call=Moomoo.call
            def now(self):return datetime.now(timezone.utc)
        quotes=Quotes();quotes.m=sdk;quotes.q=q;quotes._book_subscriptions=set();quotes._last={}
        reader=HKReadOnly(sdk,t,str(account),base/'hk_readonly_evidence.jsonl');reader.shared_read_budget=True
        transport=HKTransport(sdk,t,journal,root,audit.event);transport.shared_read_budget=True
        def books():
            held={r[0] for r in reservations.db.execute("SELECT key FROM holds WHERE state!='RELEASED'")}
            codes={json.loads(body)['code'] for tag,body in journal.db.execute('SELECT tag,request FROM submissions') if tag in held}
            return {code:quotes.book(code) for code in sorted(codes)}
        if details:
            from .hk_scan_quotes import HKScanQuotes
            yield dict(reader=reader,transport=transport,reservations=reservations,quotes=quotes,books=books,
                scan_quotes=HKScanQuotes(sdk,q,base/'hk_market/execution_quote_evidence.jsonl'))
        else:yield lambda:refresh_bound_risk(root,reader,transport,reservations,books=books)
    finally:
        if q:q.close()
        if t:t.close()
        if reservations:reservations.close()
        if journal:journal.close()
        audit.close()
