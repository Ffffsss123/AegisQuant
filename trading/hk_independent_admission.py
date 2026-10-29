"""HK entry reservation inside HKTransport's admission lock and a US joint session."""
import json
from pathlib import Path
from datetime import datetime,timezone
from .policy import SafetyError
from .hk_policy import amount
from .hk_fees import order_upper_bound
from .hk_fx import validate_accounting_binding
from .simulation_reconcile import account_session
from .simulation_admission import check_portfolio_entry
from .news import assess


def reserve_buy(root,broker,paths,request,*,account,hk_account,portfolio,archive,
                reservations,hk_cash_cap_hkd,audit):
    root=Path(root);base=root/'data/trading'
    if (request.get('side')!='BUY' or request.get('env')!='SIMULATE'
            or request.get('account')!=hk_account):raise SafetyError('Explicit HK simulation entry required')
    def read(name):return json.loads((base/name).read_text())
    with account_session(root,broker,paths,account=account,archive=archive,
                         reuse_admission=True) as (_,evidence,engines):
        now=datetime.now(timezone.utc);states={k:e.store.load() for k,e in engines.items()}
        stock=states['US_STOCK']
        if stock.get('hk_account')!=hk_account:raise SafetyError('Existing HK account binding required')
        fx=validate_accounting_binding(read('hk_fx_binding.json'),portfolio,hk_account,now)
        notional=amount(request['qty'])*amount(request['price'])
        if notional!=amount(request['notional_hkd']):raise SafetyError('HK notional mismatch')
        fees=2*amount(order_upper_bound(notional,request['qty'])['total_hkd'])
        cost=(notional+fees)/fx
        if amount(hk_cash_cap_hkd)<notional+fees:raise SafetyError('Insufficient HK virtual cash')
        books={c:broker.book(c) for s in states.values() for c,p in s['positions'].items() if p['qty']}
        controls=read('simulation_controls.json');risk=read('hk_risk_snapshot.json')
        result=check_portfolio_entry(states=states,books=books,hk_snapshot=risk,
            hk_reservations_path=base/'hk_reservations.sqlite',account=account,hk_account=hk_account,
            portfolio=portfolio,now=datetime.now(timezone.utc),risk_path=base/'simulation_loss_latch.sqlite',
            controls=controls,sleeve='HK',required_usd=cost)
        news=assess(read('news/latest.json'),request['code'],datetime.now(timezone.utc))
        audit('hk_independent_news_decision',dict(tag=request['tag'],decision=news))
        if news['global_block'] or news['exit_long'] or news['multiplier']<=0:
            raise SafetyError('HK news blocks entry')
        completed=any(amount(qty)>0 for (qty,) in reservations.db.execute('SELECT qty FROM settled_fills'))
        if controls.get('require_news_opportunity') and not completed and not news.get('entry_evidence'):
            raise SafetyError('Verified positive HK news opportunity required')
        if cost>amount(result['available_usd'])*amount(news['multiplier']):
            raise SafetyError('HK news-adjusted independent budget exceeded')
        if not 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(evidence['query_started_at'])).total_seconds()<=30:
            raise SafetyError('US joint evidence expired during HK admission')
        held=reservations.committed_and_held()
        if held>amount(risk['occupied_usd']):raise SafetyError('HK snapshot omits durable reservations')
        if reservations.db.execute('SELECT 1 FROM holds WHERE key=?',(request['tag'],)).fetchone():
            raise SafetyError('Existing HK reservation; reconcile instead of retry')
        # Invalidate before the durable reservation, including on uncertain SQLite failure.
        (base/'hk_risk_snapshot.json').unlink()
        reservations.reserve(request['tag'],cost,min(amount(500),amount(result['available_usd'])+held),identity=request)
        audit('hk_independent_entry_reserved',dict(tag=request['tag'],cost_usd=str(cost),
            admission=json.loads(json.dumps(result,default=str)),entry_news_evidence=news.get('entry_evidence',[])))
        return True
