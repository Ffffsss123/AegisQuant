"""Read-only options quotes and separate, explicitly hypothetical paper accounting."""
import fcntl,time
from collections import Counter
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
from .moomoo import Moomoo,NY
from .policy import Config,SafetyError,number,quote_price,option_candidates
from .store import Store
from .rolling_scan import atomic
from .desktop_server import ROOT,read_json,ledger
from .news import NewsGuard

VERSION='options_quote_paper_v1'
LIMIT=20
FEE=Config().option_fee_reserve_usd

class QuoteOnly(Moomoo):
    def __init__(self):
        import moomoo as m
        from .quote_feed import quote_context
        self.m=m;self.t=None;self._last={};self._calendar={};self._book_subscriptions=set()
        self.q=quote_context(m,host='127.0.0.1',port=11111)
    def bind(self,*a,**kw):raise SafetyError('Quote observer has no trading account')
    def place(self,*a,**kw):raise SafetyError('Quote observer cannot submit orders')
    def cancel(self,*a,**kw):raise SafetyError('Quote observer cannot mutate orders')
    def reset_books(self):
        # Called after a book window or while flat outside session; connection-local subscriptions only.
        codes=sorted({code for _,code in self._book_subscriptions})
        if codes:self.call('unsubscribe',self.q.unsubscribe,codes,[self.m.SubType.ORDER_BOOK])
        self._book_subscriptions.clear()

def shortlist(scan,offset=0):
    rows=[dict(code=c,**s) for c,s in scan.get('signals',{}).items()
          if s.get('liquid') and (s.get('buy') or s.get('fast',0)<s.get('slow',0))]
    rows.sort(key=lambda s:(-abs(s['strength']),s['code']))
    return rows[offset:offset+LIMIT],len(rows)

def contract(chain,snap,now):
    if chain.get('option_standard_type')!='STANDARD' or chain.get('suspension') is not False:raise SafetyError('nonstandard_or_suspended')
    if chain['code']!=snap.get('code') or chain.get('stock_owner')!=snap.get('stock_owner'):raise SafetyError('identity_mismatch')
    if snap.get('option_valid') is not True or snap.get('suspension') is not False:raise SafetyError('option_data_unverified')
    if number(chain.get('lot_size'))!=100 or number(snap.get('option_contract_multiplier'))!=100:raise SafetyError('nonstandard_multiplier')
    dte=(date.fromisoformat(chain['strike_time'][:10])-now.astimezone(NY).date()).days
    if not 30<=dte<=60:raise SafetyError('expiry')
    if not .4<=abs(number(snap.get('option_delta')))<=.6:raise SafetyError('delta')
    if number(snap.get('option_open_interest'))<1000:raise SafetyError('open_interest')
    if number(snap.get('volume'))<100:raise SafetyError('volume')
    stamp=datetime.fromisoformat(snap['update_time']).replace(tzinfo=NY)
    if not 0<=(now-stamp).total_seconds()<=120:raise SafetyError('stale_snapshot')
    # This is a preliminary cost exclusion, never a substitute for timestamped order-book execution.
    ask=number(snap.get('ask_price'));bid=number(snap.get('bid_price'))
    if not 0<bid<=ask:raise SafetyError('snapshot_book_missing')
    return dict(code=chain['code'],underlying=chain['stock_owner'],expiry=chain['strike_time'][:10],
        right=chain['option_type'],standard=True,multiplier=100,delta=number(snap['option_delta']),
        open_interest=number(snap['option_open_interest']),volume=number(snap['volume']),
        snapshot_date=stamp.date().isoformat(),snapshot_time=stamp.isoformat(),reference_ask=ask,
        reference_spread=(ask-bid)/((ask+bid)/2),strike=number(chain['strike_price']))

class Observer:
    def __init__(self,b,db,news=None):self.b=b;self.db=db;self.news=news or NewsGuard()
    def load(self,now):
        if self.db.exists():
            s=self.db.load()
            if s.get('version')!=VERSION or s.get('env')!='QUOTE_PAPER':raise SafetyError('Unexpected options observation ledger')
            return s
        s=dict(version=VERSION,env='QUOTE_PAPER',fx=.6,initial=15.,cash=15.,cap=15.,fee=FEE,position=None,
               trades=[],marks=[],claims=[],paused=False,alerts=[],cycle=None,work=None,created_at=now.isoformat())
        self.db.save(s,'initialize_quote_paper');return s
    def clock(self,now):return self.b.now() if hasattr(self.b,'now') else now
    def pause(self,s,reason):
        s['paused']=True
        if reason not in s['alerts']:s['alerts'].append(reason)
    def book(self,s,code,now):
        book=self.b.book(code);quote_price(book,'BUY',self.clock(now),True)
        self.db.event('real_option_book',dict(observed_at=self.clock(now).isoformat(),code=code,book=book))
        return book
    def book_assessment(self,work,c,now,reason):
        # At most one receipt per contract/reason per bounded work window.
        seen=work.setdefault('book_assessments',{}).setdefault(c['code'],[])
        if reason in seen:return
        self.db.event('option_book_assessment',dict(code=c['code'],reason=reason,
            observed_at=self.clock(now).isoformat(),snapshot_time=c.get('snapshot_time')))
        seen.append(reason)
    def position(self,s,scan,now,controls):
        p=s['position']
        if not p:return
        underlying=p['underlying'];sig=scan.get('signals',{}).get(underlying)
        due=(date.fromisoformat(p['expiry'])-now.astimezone(NY).date()).days<=7
        reverse=sig and ((p['right']=='CALL' and sig['fast']<=sig['slow']) or (p['right']=='PUT' and sig['fast']>=sig['slow']))
        decision=self.news(underlying,now)
        if (p['right']=='CALL' and decision.get('exit_long')) or (p['right']=='PUT' and decision.get('exit_short')):
            if not p.get('emergency_exit'):
                p['emergency_exit']=dict(detected_at=now.isoformat(),evidence=decision.get('exit_evidence',[]))
                self.db.save(s,'option_emergency_exit_latched',p['emergency_exit'])
        emergency=bool(p.get('emergency_exit'))
        s['exit_required']=bool(due or reverse or emergency)
        if not sig:s['position_warning']='持仓标的趋势信号缺失'
        else:s.pop('position_warning',None)
        try:
            book=self.book(s,p['code'],now);now=self.clock(now);bid=quote_price(book,'SELL',now,True)
        except Exception as exc:
            s['valuation_valid']=False;s['position_warning']='期权退出报价不可验证：'+str(exc)
            if due or reverse or emergency:self.pause(s,'期权需要退出，但有效报价缺失；需检查')
            return
        equity=s['cash']+bid*100-s['fee'];s['valuation_valid']=True;s['equity_usd']=equity
        s['marks'].append(dict(time=now.isoformat(),equity_usd=equity))
        if equity<=0:self.pause(s,'期权研究额度耗尽，停止模拟开仓')
        if (due or reverse or emergency) and not controls.get('stopped'):
            s['cash']+=bid*100-s['fee']
            s['trades'].append(dict(side='SELL',code=p['code'],time=now.isoformat(),price=bid,qty=1,
                fee=s['fee'],reason='emergency_news' if emergency else 'expiry_7_days' if due else 'trend_reversal',news_evidence=p.get('emergency_exit'),hypothetical=True))
            s['position']=None;s['equity_usd']=s['cash'];s['exit_required']=False
            self.db.save(s,'hypothetical_exit')
    def enter(self,s,rows,scan,paper,controls,now):
        if s.get("shared_capital"):
            from .shared_capital import admission_lock
            root=self.db.path.parent.parent.parent
            with admission_lock(root):
                paper=ledger(self.db.path.parent/"paper.sqlite")
                controls=read_json(self.db.path.parent/"simulation_controls.json")
                return self._enter(s,rows,scan,paper,controls,self.clock(now))
        return self._enter(s,rows,scan,paper,controls,now)
    def _enter(self,s,rows,scan,paper,controls,now):
        if paper.get('capital_mode')=='independent_sleeves_v1':
            # This observer remains quote-only: publish a hint for the separately
            # reconciled execution sleeve, never allocate or trade paper funds.
            execution=ledger(self.db.path.parent/'option_transport.sqlite')
            if (execution.get('capital_mode')!='independent_sleeves_v1'
                    or execution.get('sleeve')!='US_OPTION' or execution.get('env')!='SIMULATE'
                    or execution.get('portfolio_id')!=paper.get('portfolio_id')
                    or execution.get('probe_phase') not in ('cancel_test','buy')):return
            choices=option_candidates(rows,scan['signals'],now.astimezone(NY).date(),now,
                min(500,execution['cash'])-5,5)
            choices=[c for c in choices if not execution.get('probe_code') or c['code']==execution['probe_code']]
            if choices:
                atomic(self.db.path.parent/'options_broker_candidate.json',dict(created_at=now.isoformat(),
                    contract=choices[0],source='independent_quote_hint',submission_enabled=False))
                self.db.event('independent_quote_hint',dict(code=choices[0]['code']))
            return
        if s['position'] or s['paused'] or controls.get('paused') or controls.get('stopped') or paper.get('paused') or paper.get('stopped'):return
        if paper.get('env')!='SIMULATE' or paper.get('purpose')!='strategy_simulation':return
        if now.astimezone(NY).date().isoformat()<=paper.get('entry_after_session',''):
            from .swing_engine import SwingEngine
            allowed=s.get('temporary_entry_session')==now.astimezone(NY).date().isoformat() and SwingEngine.temporary_entry_active(paper,now)
            if not allowed:return
        marks=paper.get('marks',[])
        if s.get('budget_currency')=='USD':
            if paper.get('portfolio_id')!=s.get('portfolio_id') or paper.get('option_budget_usd')!=s['cap']:return
        elif paper.get('fx')!=s['fx']:return
        if not marks or not 0<=(now-datetime.fromisoformat(marks[-1]['time'])).total_seconds()<=30:return
        from .hk_shared_admission import exposure
        hk=exposure(paper,now)
        if hk['entry_block']:return
        if paper['initial']-marks[-1]['equity_usd']-hk['total_net_pnl_usd']+max(0,s['initial']-s.get('equity_usd',s['cash']))>=paper['loss_limit']:return
        # Reserve the eventual exit fee as well, without assuming future gains finance it.
        cap=min(s['cap'],s['cash']-s['fee'],paper['cash']-s['fee'])
        if s.get('shared_capital'):
            from .shared_capital import available_cash
            cap=min(cap,available_cash(paper,s)-s['fee'])
            s['shared_available_usd']=max(0,cap+s['fee'])
        choices=option_candidates(rows,scan['signals'],now.astimezone(NY).date(),now,cap,s['fee'])
        if not choices:return
        c=choices[0];claim=now.astimezone(NY).date().isoformat()+':'+c['underlying']
        if claim in s['claims']:return
        decision=self.news(c['underlying'],now);self.db.event('option_news_decision',decision)
        if decision['multiplier']<=0 or c['ask']*100+s['fee']>cap*decision['multiplier']+1e-8:return
        root=self.db.path.parent.parent.parent
        from .option_transport import CONTROL,active
        request=read_json(root/'data/trading'/CONTROL)
        if request.get('requested'):
            if not active(root):atomic(root/'data/trading/options_broker_candidate.json',dict(created_at=now.isoformat(),contract=c))
            s['status']='waiting_broker_transport';self.db.save(s,'broker_transport_candidate',dict(code=c['code']))
            return
        if active(root):return
        s['claims'].append(claim);s['cash']-=c['ask']*100+s['fee']
        s['position']=dict(c,entry_time=now.isoformat(),entry_price=c['ask'])
        s['trades'].append(dict(side='BUY',code=c['code'],time=now.isoformat(),price=c['ask'],qty=1,fee=s['fee'],hypothetical=True))
        s['valuation_valid']=False
        self.db.save(s,'hypothetical_entry')
    def step(self,s,scan,paper,controls,now):
        s['controls']=dict(paused=bool(controls.get('paused') or paper.get('paused')),stopped=bool(controls.get('stopped') or paper.get('stopped')))
        session=self.b.execution_session(now,strict=False)
        if not session:
            # Release only this flat research connection's books, once per off-session stretch.
            # Keep interrupted work and raw receipts for diagnosis; never clear a held position.
            if s.get('env')=='QUOTE_PAPER' and not s.get('position') and getattr(self.b,'_off_session_books_released',False) is not True:
                self.b.reset_books()
                self.b._off_session_books_released=True
                self.db.event('off_session_books_released',dict(observed_at=now.isoformat()))
            s['status']='waiting_regular_session';return
        self.b._off_session_books_released=False
        current=scan.get('complete') and scan.get('date')==session[1]
        # DTE exits remain available when the daily scan is unavailable; never use old trend signals.
        self.position(s,scan if current else dict(signals={}),now,s['controls'])
        marks=paper.get('marks',[])
        if marks and s.get('valuation_valid',not bool(s['position'])):
            value=s.get('equity_usd',s['cash']) if s['position'] else s['cash']
            if paper['initial']-marks[-1]['equity_usd']+s['initial']-value>=paper['loss_limit']:self.pause(s,'股票与期权研究合并亏损达到上限，停止纸面开仓')
        if s['position']:s['status']='position_monitoring';return
        if not current:s['status']='waiting_daily_scan';return
        key=session[0]+':'+str(scan.get('generated_at'))
        cycle=s.get('cycle')
        cursor=s.get('scan_cursor',{})
        offset=cursor.get('offset',0) if cursor.get('key')==key else 0
        if cycle and cycle.get('key')==key:offset=cycle.get('offset',0)
        selected,total=shortlist(scan,offset)
        if not selected and total:
            offset=0;selected,total=shortlist(scan)
        s['watch_underlyings']=[r['code'] for r in selected];s['eligible_underlyings']=total
        if not cycle or cycle['key']!=key:
            s['cycle']=dict(key=key,offset=offset,index=0,started_at=now.isoformat(),counts={},scanned=0,contracts=0);s['work']=None
            cycle=s['cycle']
        if cycle['index']>=len(selected):
            s['status']='round_complete_no_entry'
            if not cycle.get('finished_at'):cycle['finished_at']=now.isoformat()
            if (now-datetime.fromisoformat(cycle['finished_at'])).total_seconds()>=1800:
                s['scan_cursor']=dict(key=key,offset=(offset+len(selected))%total if total else 0)
                s['cycle']=None
            return
        s['status']='scanning'
        if s.get('next_step_at') and now<datetime.fromisoformat(s['next_step_at']):return
        work=s.get('work');counts=Counter(cycle['counts'])
        if work is None:
            item=selected[cycle['index']];code=item['code'];rows=self.b.option_chain(code,now.astimezone(NY).date())
            self.db.event('real_option_chain',dict(underlying=code,observed_at=self.clock(now).isoformat(),rows=rows))
            right='CALL' if item['buy'] else 'PUT'
            static=[r for r in rows if r.get('option_type')==right and r.get('option_standard_type')=='STANDARD' and r.get('suspension') is False]
            counts['nonstandard_suspended_or_other_direction']+=len(rows)-len(static)
            s['work']=dict(underlying=code,chain=static,offset=0,qualified=[],book_started=None)
            cycle['contracts']+=len(rows)
        elif work['offset']<len(work['chain']):
            group=work['chain'][work['offset']:work['offset']+100]
            rows=self.b.snapshots([r['code'] for r in group]);self.db.event('real_option_snapshots',dict(observed_at=self.clock(now).isoformat(),rows=rows))
            bycode={r['code']:r for r in rows}
            for c in group:
                try:
                    row=contract(c,bycode[c['code']],self.clock(now))
                    if row['reference_ask']*100+2*s['fee']>min(s['cap'],s['cash']):counts['budget_including_roundtrip_reserve']+=1
                    else:work['qualified'].append(row)
                except (SafetyError,ValueError,TypeError,KeyError) as exc:counts[str(exc) if isinstance(exc,SafetyError) else 'missing_invalid_data']+=1
            work['offset']+=len(group)
        else:
            qualified=work['qualified']
            if len(qualified)>40:counts['subscription_capacity']+=len(qualified);qualified=[]
            if qualified:
                if not work.get('book_started'):work['book_started']=now.isoformat()
                valid=[]
                for c in qualified:
                    try:
                        if (now-datetime.fromisoformat(c['snapshot_time'])).total_seconds()>120:
                            self.book_assessment(work,c,now,'snapshot_expired');continue
                        valid.append(dict(c,book=self.book(s,c['code'],now)))
                        self.book_assessment(work,c,now,'valid_book')
                    except (SafetyError,KeyError,ValueError) as exc:
                        known={'Stale/future quote':'quote_expired','Spread too wide':'wide_spread',
                               'Invalid bid/ask':'invalid_bid_ask','Quote timezone absent':'missing_timezone'}
                        status=getattr(exc,'status',None)
                        reason=(status if status in ('quote_expired','connection_interrupted') else
                                known.get(str(exc),'quote_unavailable' if isinstance(exc,SafetyError) else 'invalid_quote_data'))
                        self.book_assessment(work,c,now,reason)
                self.enter(s,valid,scan,paper,controls,self.clock(now))
                if (now-datetime.fromisoformat(work['book_started'])).total_seconds()<120:return
                if not s['position']:self.b.reset_books()
                counts['qualified_before_final_quote_news_budget_checks']+=len(qualified)
                counts['valid_books_last_check']+=len(valid)
            cycle['index']+=1;cycle['scanned']+=1;s['work']=None
        cycle['counts']=dict(counts);s['next_step_at']=(now+timedelta(seconds=10)).isoformat()
    def report(self,s,now,root=ROOT):
        equity=s.get('equity_usd',s['cash']) if not s['position'] or s.get('valuation_valid') else None
        weekly={};previous=s['initial'];weeks=[]
        for mark in s['marks']:
            d=datetime.fromisoformat(mark['time']).astimezone(NY).date();weekly[(d-timedelta(days=d.weekday())).isoformat()]=mark['equity_usd']
        for week,v in sorted(weekly.items()):weeks.append(dict(week=week,pnl_usd=v-previous));previous=v
        atomic(Path(root)/'data/trading/options.report.json',dict(version=VERSION,status=s.get('status'),updated_at=now.isoformat(),
            temporary_entry_session=s.get('temporary_entry_session'),mode='真实报价纸面模拟 · 假设按买卖盘成交',live_enabled=False,broker_orders_submitted=0,acceptance=False,
            shared_capital=s.get('shared_capital',False),shared_available_usd=s.get('shared_available_usd'),
            display_currency='USD',budget_usd=s['cap'],fee_reserve_usd=s['fee'],cash_usd=s['cash'],equity_usd=equity,pnl_usd=equity-s['initial'] if equity is not None else None,
            position=s['position'],trades=len(s['trades']),closed_trades=sum(t['side']=='SELL' for t in s['trades']),weeks=weeks,
            valuation_at=s['marks'][-1]['time'] if s['marks'] else None,controls=s.get('controls',{}),paused=s['paused'],alerts=s['alerts'],warning=s.get('position_warning'),error=s.get('error'),retry_at=s.get('retry_at'),
            scope_limit=LIMIT,eligible_underlyings=s.get('eligible_underlyings'),watch_underlyings=s.get('watch_underlyings',[]),
            cycle=s.get('cycle'),current_underlying=(s.get('work') or {}).get('underlying'),
            note=('与股票共用 500 USD，账面初始值不代表额外资金；按信号及成本筛选，尚无跨股票／期权预期利润模型。' if s.get('shared_capital') else '')+'独立研究记录，不计入股票策略收益；不证明限价能成交。双边各预留 5 USD，未验证账户实际收费。'))

def main():
    root=ROOT;path=root/'data/trading/options_observer.sqlite'
    with open(str(path)+'.runner.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        db=Store(path);b=None
        try:
            while True:
                now=datetime.now(timezone.utc)
                try:
                    e=Observer(b,db);s=e.load(now)
                    if s.get('failures',0)<=3 and (not s.get('retry_at') or now>=datetime.fromisoformat(s['retry_at'])):
                        if b is None:b=QuoteOnly();e=Observer(b,db)
                        e.step(s,read_json(root/'data/trading/market/scan.json'),ledger(root/'data/trading/paper.sqlite'),read_json(root/'data/trading/simulation_controls.json'),now)
                        s.pop('error',None);s.pop('retry_at',None);s['failures']=0
                        db.save(s,'observer_step')
                    e.report(s,now)
                except Exception as exc:
                    s=db.load() if db.exists() else Observer(None,db).load(now)
                    attempts=s.get('failures',0);delay=(300,900,1800)[min(attempts,2)]
                    s.update(error=str(exc),status='needs_attention' if attempts>=3 else 'connection_or_data_error',failures=attempts+1,retry_at=(now+timedelta(seconds=delay)).isoformat() if attempts<3 else None,valuation_valid=False)
                    if s.get('position'):s['position_warning']='连接或数据中断；已有期权纸面仓位的估值和退出需要检查'
                    db.save(s,'observer_error');Observer(None,db).report(s,now)
                    if b:b.close();b=None
                time.sleep(5)
        finally:
            if b:b.close()
            db.close()

if __name__=='__main__':main()
