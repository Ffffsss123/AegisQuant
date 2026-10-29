from datetime import datetime, date, timedelta
from uuid import uuid4
from .policy import Config, SafetyError, number, quote_price, whole_qty, option_candidates

TERMINAL={'FILLED_ALL','CANCELLED_ALL','CANCELLED_PART','FAILED','DISABLED','DELETED','SUBMIT_FAILED'}

class Engine:
    def __init__(self,broker,store,config,news=None):
        self.b=broker;self.store=store;self.c=config.validate()
        from .news import NewsGuard
        self.news=news if news is not None else NewsGuard()
    def clock(self,fallback):
        return self.b.now() if hasattr(self.b,'now') else fallback
    def alert(self,s,message):
        if message not in s['alerts']: s['alerts'].append(message)
        s['paused']=True
        self.store.save(s,'alert',{'message':message})
    def bind(self,account,env,fx,now,extra=None):
        if self.store.exists(): raise SafetyError('State is already bound; cannot reset budget or account')
        fx=number(fx,'NZDUSD')
        if not 0<fx<2: raise SafetyError('Invalid reference FX')
        self.b.bind(account,env)
        positions=self.b.positions();orders=self.b.orders()
        if any(o['status'] not in TERMINAL for o in orders):
            raise SafetyError('Resolve existing open orders before binding')
        if self.b.settled_cash()<self.c.budget(fx):
            raise SafetyError('Insufficient settled USD cash for the chosen budget')
        s=dict(account=str(account),env=env,fx=fx,initial=self.c.budget(fx),
            loss_limit=self.c.loss(fx),cash=self.c.budget(fx),
            bound_at=now.isoformat(),baseline=positions,positions={},orders={},
            processed=[],paused=False,stopped=False,alerts=[],marks=[],
            enabled_stocks=False,enabled_options=False,option_validation=False)
        if self.c.budget_usd is not None:s.update(budget_currency='USD',option_budget_usd=self.c.option_cap(fx))
        if extra:s.update(extra)
        self.store.save(s,'bind',{'env':env})
        return s
    def load(self):
        s=self.store.load()
        self.b.bind(s['account'],s['env'])
        if abs(s['initial']-self.c.budget(s['fx']))>1e-8 or abs(s['loss_limit']-self.c.loss(s['fx']))>1e-8:
            raise SafetyError('Bound budget/loss settings are immutable')
        return s
    def reconcile(self,s,now):
        remote=self.b.orders(s['bound_at'][:10])
        by_id={o['id']:o for o in remote};by_tag={}
        for o in remote:
            if o.get('tag'): by_tag.setdefault(o['tag'],[]).append(o)
        known_ids=set()
        for tag,local in s['orders'].items():
            matches=by_tag.get(tag,[])
            if len(matches)>1: self.alert(s,'Duplicate broker tag '+tag);continue
            r=by_id.get(local.get('id')) or (matches[0] if matches else None)
            if r is None:
                if local['status'] not in TERMINAL: self.alert(s,'Unresolved order '+tag)
                continue
            known_ids.add(r['id'])
            if r['code']!=local['code'] or r['side']!=local['side'] or abs(r['qty']-local['qty'])>1e-8:
                self.alert(s,'Order identity/quantity mismatch '+tag);continue
            q=number(r['filled']);price=number(r['average'])
            if q<local['booked_qty'] or q>local['qty'] or (q>0 and price<=0):
                self.alert(s,'Invalid cumulative fill '+tag);continue
            mult=local['multiplier'];notional=q*price*mult
            dq=q-local['booked_qty'];dn=notional-local['booked_notional']
            p=s['positions'].setdefault(local['code'],dict(qty=0,kind=local['kind'],multiplier=mult,
                cost=0,realized=0,fees=0,expiry=local.get('expiry'),underlying=local.get('underlying'),right=local.get('right')))
            if local['side']=='SELL' and dq>p['qty']+1e-8:
                self.alert(s,'Sell exceeds owned position '+tag);continue
            if local['side']=='BUY':
                s['cash']-=dn;p['qty']+=dq;p['cost']+=dn
            else:
                if dq>p['qty']+1e-8: self.alert(s,'Sell exceeds owned position '+tag);continue
                basis=p['cost']/p['qty']*dq if p['qty'] else 0
                s['cash']+=dn;p['qty']-=dq;p['cost']-=basis;p['realized']+=dn-basis
            fee=local['booked_fee'] if local.get('fee_final') and local['status'] in TERMINAL and r['status'] in TERMINAL else None
            if fee is None and r['status'] in TERMINAL and q>0:
                try: fee=self.b.fee(r['id'])
                except Exception: pass
            if fee is None:
                fee=local['fee_reserve'] if q>0 else 0
                local['fee_final']=q==0 or s['env']=='SIMULATE'
            else:
                fee=number(fee,'order fee');local['fee_final']=True
                if fee<0: raise SafetyError('Invalid fee')
            df=fee-local['booked_fee'];s['cash']-=df;p['fees']+=df
            local.update(id=r['id'],status=r['status'],booked_qty=q,booked_notional=notional,booked_fee=fee)
            self.store.save(s,'order_sync',{'tag':tag,'status':r['status'],'filled':q})
        for r in remote:
            if r['id'] not in known_ids and r['status'] not in TERMINAL:
                self.alert(s,'External open order detected')
        expected=dict(s['baseline'])
        for code,p in s['positions'].items(): expected[code]=expected.get(code,0)+p['qty']
        actual=self.b.positions()
        different={code for code in set(expected)|set(actual) if abs(expected.get(code,0)-actual.get(code,0))>1e-7}
        if different:
            pending={o['code'] for o in s['orders'].values() if o['status'] not in TERMINAL}
            if different<=pending and not getattr(self,'_position_recheck',False):
                # Orders and positions are separate snapshots; a fill can occur between them.
                # Retry once, book only broker-confirmed fills, and never infer a fill from quantity.
                self.store.save(s,'position_snapshot_recheck',dict(codes=sorted(different)))
                self._position_recheck=True
                try:return self.reconcile(s,now)
                finally:self._position_recheck=False
            self.alert(s,'Position mismatch / manual activity / corporate action')
        if s['cash']<-.01: self.alert(s,'Strategy cash below zero')
        return s
    def news_check(self,s,code,now):
        decision=self.news(code,now)
        s.setdefault('news_status',{})[code]=decision
        self.store.save(s,'news_decision',decision)
        return decision['multiplier']
    def manage_orders(self,s,now,reconciled=False):
        changed=False
        for tag in list(s['orders']):
            o=s['orders'][tag]
            if o['status'] in TERMINAL: continue
            expired=(now-datetime.fromisoformat(o['created'])).total_seconds()>=60
            if not o.get('id'): continue
            must_cancel=expired or (s['paused'] and o['side']=='BUY') or s['stopped']
            # News availability cannot delay an already-required safety cancellation.
            news_blocked=not must_cancel and o['side']=='BUY' and self.news_check(s,o.get('underlying') or o['code'],self.clock(now))==0
            if must_cancel or news_blocked:
                # Mark before network mutation; a lost acknowledgement is never treated as a cancellation.
                o['cancel_requested']=True;self.store.save(s,'cancel_intent',{'tag':tag})
                changed=True
                try: self.b.cancel(o['id'])
                except Exception as exc:
                    try:self.reconcile(s,now)
                    except Exception:pass
                    # Joint reconciliation replaces nested order dictionaries.
                    o=s['orders'][tag]
                    if o['status'] not in TERMINAL:self.alert(s,'Cancel not confirmed '+tag+': '+str(exc))
                    else:self.store.save(s,'cancel_terminal_race',dict(tag=tag,status=o['status'],error=str(exc)))
                if o['kind']=='OPTION' and o['side']=='SELL' and o['booked_qty']<o['qty']:
                    self.alert(s,'Option exit unfilled: manual action required '+o['code'])
        if changed or not reconciled:self.reconcile(s,now)
    def mark(self,s,now):
        equity=s['cash'];parts={'STOCK':0.,'OPTION':0.};fees=0
        for code,p in s['positions'].items():
            value=0
            if p['qty']:
                value=p['qty']*p['multiplier']*self.b.mark(code,now)
            parts[p['kind']]+=p['realized']+value-p['cost']-p['fees']
            fees+=p['fees'];equity+=value
        if s['initial']-equity>=s['loss_limit']-1e-8:
            self.alert(s,'Cumulative loss limit reached')
        record=dict(time=now.isoformat(),equity_usd=equity,equity_nzd=equity/s['fx'],
                    stock_pnl_usd=parts['STOCK'],option_pnl_usd=parts['OPTION'],fees_usd=fees)
        s['marks'].append(record);self.store.save(s,'valuation')
        return equity
    def blocked_orders(self,s):
        return any(o['status'] not in TERMINAL or not o.get('fee_final',False) for o in s['orders'].values())
    def execution_quote(self,s,item,book,now):
        return quote_price(book,item['side'],now,item['kind']=='OPTION')
    def sell_without_mark(self,s,item):return False
    def submit(self,s,code,side,qty,price,now,kind='STOCK',contract=None,execution_purpose='strategy',exit_reason=None):
        if qty<=0 or int(qty)!=qty or not price>0: raise SafetyError('Whole positive order required')
        # Independent funding is not executable until the joint submission path
        # is installed. A ledger flag alone must never select legacy admission.
        if side=='BUY' and s.get('capital_mode') is not None and not (s.get('env')=='SIMULATE' and getattr(self,'_independent_entry_session',False) is True and callable(getattr(self,'before_order_intent',None))):
            raise SafetyError('Independent capital submission path is not enabled')
        if s['stopped'] or (side=='BUY' and s['paused']): raise SafetyError('Trading paused/stopped')
        if s['env']=='REAL':
            enabled=(s['enabled_stocks'] and self.c.live_stocks) if kind=='STOCK' else (s['enabled_options'] and self.c.live_options and s['option_validation'])
            if not enabled: raise SafetyError('Live product switch is disabled')
            from zoneinfo import ZoneInfo
            enabled_at=s.get('stocks_enabled_at' if kind=='STOCK' else 'options_enabled_at')
            market_zone=getattr(self.b,'timezone',ZoneInfo('America/New_York'))
            if not enabled_at or now.astimezone(market_zone).date()<=datetime.fromisoformat(enabled_at).astimezone(market_zone).date():
                raise SafetyError('First live execution must wait until a later trading session')
        if self.blocked_orders(s): raise SafetyError('Pending/unknown order or unsettled fees')
        # Reconcile immediately before every mutation and recheck the risk state.
        self.reconcile(s,now)
        if self.blocked_orders(s): raise SafetyError('Orders changed before submission')
        if s['stopped']: raise SafetyError('Reconciliation stopped trading')
        if side=='BUY' and s['paused']: raise SafetyError('Reconciliation paused buying')
        p=s['positions'].get(code,{'qty':0})
        actual=self.b.positions()
        if not self.b.execution_session(self.clock(now),strict=False):raise SafetyError('Outside regular execution hours')
        fresh=self.b.book(code)
        current=self.execution_quote(s,dict(code=code,side=side,kind=kind,exit_reason=exit_reason),fresh,self.clock(now))
        price=min(price,current) if side=='BUY' else max(price,current)
        if code in s['baseline'] and s['baseline'][code]!=0: raise SafetyError('External holding cannot be managed')
        mult=100 if kind=='OPTION' else 1
        reserve=self.c.option_fee_reserve_usd if kind=='OPTION' else self.c.fee_reserve_usd
        cost=qty*price*mult+reserve
        if side=='SELL':
            if p.get('kind')!=kind or p.get('multiplier')!=mult:
                raise SafetyError('Exit instrument differs from owned position')
            if qty>p['qty'] or qty>actual.get(code,0): raise SafetyError('No selling external/short positions')
        else:
            if p['qty']>0: raise SafetyError('No position additions')
            if cost>min(s['cash'],self.b.settled_cash())+1e-8: raise SafetyError('Insufficient cash incl fees')
            if kind=='STOCK':
                count=sum(p['qty']>0 and p['kind']=='STOCK' for p in s['positions'].values())
                from .hk_shared_admission import occupied_slots
                if not (s.get('env')=='SIMULATE' and s.get('capital_mode')=='independent_sleeves_v1'
                        and getattr(self,'_independent_entry_session',False) is True):
                    count+=occupied_slots(s,self.clock(now))
                if count>=self.c.max_positions or cost>s['initial']*self.c.allocation: raise SafetyError('Stock allocation limit')
            else:
                independent=(s.get('env')=='SIMULATE' and s.get('capital_mode')=='independent_sleeves_v1'
                    and getattr(self,'_independent_entry_session',False) is True)
                option_count=sum(p['qty']>0 and p['kind']=='OPTION' for p in s['positions'].values())
                if qty!=1 or option_count>=(2 if independent else 1):raise SafetyError('Option position limit')
                if not contract or contract.get('standard') is not True or contract.get('multiplier')!=100: raise SafetyError('Unverified option contract')
                if cost>self.c.option_cap(s['fx']): raise SafetyError('Option cap incl fees')
        if side=='BUY':
            subject=(contract or {}).get('underlying') or code
            factor=self.news_check(s,subject,self.clock(now))
            cap=s['initial']*self.c.allocation if kind=='STOCK' else self.c.option_cap(s['fx'])
            if factor<=0 or cost>cap*factor+1e-8:raise SafetyError('News risk filter blocks entry or reduces allocation')
        controls=self.controls() if hasattr(self,'controls') else {}
        if controls.get('stopped') or (side=='BUY' and controls.get('paused')):
            s['paused']=True
            if controls.get('stopped'):s['stopped']=True
            self.store.save(s,'simulation_control')
            raise SafetyError('Simulation control blocks submission')
        if side=='BUY':
            from .capital import require_no_pending_migration
            require_no_pending_migration(controls)
        opportunity=[]
        news_entry_completed=any(o.get('side')=='BUY' and o.get('booked_qty',0)>0 and o.get('entry_news_evidence') for o in s['orders'].values())
        if side=='BUY' and controls.get('require_news_opportunity') and not news_entry_completed:
            opportunity=s.get('news_status',{}).get(subject,{}).get('entry_evidence',[])
            # Positive issuer news supports stock/long calls, never a bearish put.
            if not opportunity or (kind=='OPTION' and (contract or {}).get('right')!='CALL'):
                self.store.save(s,'news_opportunity_wait',dict(code=code,subject=subject))
                raise SafetyError('Verified positive news opportunity required before buying')
        if side=='BUY' and s.get('capital_mode') is not None and hasattr(self,'independent_entry_budget'):
            self.independent_entry_budget(s,dict(qty=qty,price=price,kind=kind,code=code))
        stock_plan=self.entry_plan(s,code,qty,price) if side=='BUY' and kind=='STOCK' else None
        # Resolve transport permission before persisting an intent. A scoped
        # read-only broker rejects access here; it never attempted submission.
        place=self.b.place
        tag='q'+uuid4().hex
        o=dict(code=code,side=side,qty=qty,price=price,kind=kind,multiplier=mult,
               created=now.isoformat(),status='INTENT',id=None,booked_qty=0,booked_notional=0,
               booked_fee=0,fee_reserve=reserve,fee_final=False)
        o['execution_purpose']=execution_purpose
        if opportunity:o['entry_news_evidence']=opportunity
        if exit_reason:o['exit_reason']=exit_reason
        if stock_plan:o['stock_plan']=stock_plan
        if contract: o.update({k:contract[k] for k in ('expiry','underlying','right')})
        preflight=getattr(self,'before_order_intent',None)
        if preflight is not None:preflight(s,o,tag)
        s['orders'][tag]=o;self.store.save(s,'order_intent',{'tag':tag})
        try:
            o['id']=place(code,side,qty,price,tag);o['status']='SUBMITTED'
            self.store.save(s,'order_ack',{'tag':tag})
        except Exception as exc:
            o['status']='UNKNOWN';self.alert(s,'Submission uncertain; reconcile before any retry: '+str(exc))
        return tag
    def tick(self,scan,now):
        s=self.load()
        self.reconcile(s,now)
        try: self.mark(s,now)
        except Exception as exc: self.alert(s,'Valuation unavailable: '+str(exc))
        self.manage_orders(s,now,reconciled=True)
        if s['stopped'] or self.blocked_orders(s): return s
        session=self.b.execution_session(now)
        if not session: return s
        day,previous=session
        if scan.get('date')!=previous:
            s['data_block']='等待上一交易日的有效扫描';self.store.save(s,'scan_wait');return s
        missing=[c for c,p in s['positions'].items() if p['qty'] and (p.get('underlying') or c) not in scan.get('signals',{})]
        if missing:scan=dict(scan,complete=False)
        s['data_block']=('持仓退出信号缺失：'+', '.join(missing)) if missing else (None if scan.get('complete') else '扫描未完成，暂停新买入')
        self.store.save(s,'scan_readiness')
        # One durable decision per session; a crash sacrifices a trade rather than duplicating it.
        if day in s['processed']: return s
        s['processed'].append(day);self.store.save(s,'session_claim',{'day':day})
        exits=[]
        for code,p in s['positions'].items():
            if not p['qty']: continue
            signal=scan.get('signals',{}).get(p.get('underlying') or code)
            if p['kind']=='OPTION':
                near=(date.fromisoformat(p['expiry'])-date.fromisoformat(day)).days<=7
                reverse=signal and ((p['right']=='CALL' and signal['sell']) or (p['right']=='PUT' and signal['fast']>=signal['slow']))
                if near or reverse: exits.append((code,p))
            elif signal and signal['sell']: exits.append((code,p))
        # Execute one at a time; settling/cancellation handled before the next item.
        queue=[dict(code=code,side='SELL',kind=p['kind'],qty=p['qty']) for code,p in exits]
        if not s['paused'] and scan.get('complete') and day>s.get('entry_after_session',''):
            count=sum(p['qty']>0 and p['kind']=='STOCK' for p in s['positions'].values())
            slots=max(0,self.c.max_positions-count+sum(p['kind']=='STOCK' for _,p in exits))
            selected=[c for c in scan['candidates'] if not s['positions'].get(c['code'],{}).get('qty') and not s['baseline'].get(c['code'],0)][:slots]
            queue += [dict(code=c['code'],side='BUY',kind='STOCK') for c in selected]
        s['queue']=queue;s['queue_day']=day;self.store.save(s,'decision_queue')
        return s
    def entry_plan(self,s,code,qty,price):return None
    def order_quantity(self,s,item,price,factor):
        return item.get('qty') or whole_qty(min(s['cash'],self.b.settled_cash()),s['initial']*self.c.allocation*factor,price,self.c.fee_reserve_usd)
    def quote_waitable(self,s,item,exc):
        from .quote_feed import QuoteUnavailable
        return isinstance(exc,QuoteUnavailable) or str(exc)=='Stale/future quote'
    def quote_deferred(self,s,item,now,exc):
        if not self.quote_waitable(s,item,exc):return False
        import time
        key=s.get('queue_day','')+item['code']+item['side']
        wait=s.get('quote_wait')
        if not wait or wait.get('key')!=key:
            wait=dict(key=key,started_at=now.isoformat(),code=item['code']);s['quote_wait']=wait
        if not hasattr(self,'_quote_wait_starts'):self._quote_wait_starts={}
        age=max(0,(now-datetime.fromisoformat(wait['started_at'])).total_seconds())
        start=self._quote_wait_starts.setdefault(key,time.monotonic()-age)
        elapsed=max(age,time.monotonic()-start)
        wait.update(status=getattr(exc,'status','quote_expired'),elapsed_seconds=round(elapsed,1))
        if elapsed>=120:
            if s.get('queue') and s['queue'][0]==item:s['queue'].pop(0)
            s.pop('quote_wait',None);self.store.save(s,'skipped',{'code':item['code'],'reason':'Quote wait exceeded 120 seconds'})
        else:self.store.save(s,'waiting_quote',wait)
        return True
    def drain(self,scan,now):
        s=self.load();session=self.b.execution_session(now,strict=False)
        if not session or s.get('queue_day')!=session[0] or s['stopped'] or not s.get('queue'): return
        # Readiness sessions cannot consume a durable exit decision. Check
        # transport access before reconciliation or claiming the queue item.
        self.b.place
        self.reconcile(s,now)
        try:self.mark(s,now)
        except Exception as exc:
            self.alert(s,'Valuation unavailable: '+str(exc))
            self.manage_orders(s,now,reconciled=True)
            if not self.sell_without_mark(s,(s.get('queue') or [{}])[0]):return
        self.manage_orders(s,now,reconciled=True)
        if self.blocked_orders(s):return
        queue=s.get('queue',[])
        if not queue:return
        item=queue[0]
        if item['side']=='BUY' and (s['paused'] or s.get('data_block') or not scan.get('complete') or scan.get('date')!=session[1]):
            queue.pop(0);s.pop('quote_wait',None);self.store.save(s,'entry_blocked');return
        wait=s.get('quote_wait')
        if wait and wait.get('code')==item['code'] and (now-datetime.fromisoformat(wait['started_at'])).total_seconds()>=120:
            from .quote_feed import QuoteUnavailable
            self.quote_deferred(s,item,now,QuoteUnavailable('Quote wait expired','quote_wait_timeout'));return dict(status='skipped',reason='Quote wait exceeded 120 seconds',replaceable=True)
        old_orders=set(s['orders']);claimed=False
        try:
            book=self.b.book(item['code'])
            price=self.execution_quote(s,item,book,self.clock(now))
            factor=self.news_check(s,item['code'],self.clock(now)) if item['side']=='BUY' else 1.
            qty=self.order_quantity(s,item,price,factor)
            queue.pop(0);claimed=True;self.store.save(s,'queue_claim',item)
            if qty==0:
                s.pop('quote_wait',None);self.store.save(s,'skipped',{'code':item['code'],'reason':'Budget or news filter'});return dict(status='skipped',reason='Budget or news filter',replaceable=True)
            self.submit(s,item['code'],item['side'],qty,price,now,item['kind'],execution_purpose=item.get('execution_purpose','strategy'),exit_reason=item.get('exit_reason'))
            s.pop('quote_wait',None);self.store.save(s,'queue_submitted')
        except Exception as exc:
            from .quote_feed import QuoteUnavailable
            deferred=self.quote_waitable(s,item,exc)
            # Pre-submit joint refresh may replace the queue dictionary/list.
            queue=s.setdefault('queue',[])
            if deferred and set(s['orders'])==old_orders:
                if claimed:queue.insert(0,item)
                self.quote_deferred(s,item,now,exc);return
            if not claimed:queue.pop(0)
            s.pop('quote_wait',None);self.store.save(s,'skipped',{'code':item['code'],'reason':str(exc)})
            if item['kind']=='OPTION' and item['side']=='SELL':self.alert(s,'Option exit failed; manual action required')
            return dict(status='skipped',reason=str(exc),replaceable=str(exc)=='Spread too wide' and set(s['orders'])==old_orders)
    def option_entry(self,scan,contracts,now):
        s=self.load();session=self.b.execution_session(now)
        if not session or scan.get('date')!=session[1] or not scan.get('complete'):return
        key='options:'+session[0]
        if key in s['processed'] or s['paused'] or s['stopped']:return
        if not s['option_validation'] and s['env']=='REAL':return
        self.reconcile(s,now)
        if self.blocked_orders(s) or s['paused']:return
        self.mark(s,now)
        if s['paused']:return
        selected=option_candidates(contracts,scan['signals'],date.fromisoformat(session[0]),now,
                                   min(self.c.option_cap(s['fx']),s['cash']),self.c.option_fee_reserve_usd)
        s['processed'].append(key);self.store.save(s,'option_session_claim')
        if selected:
            c=selected[0]
            self.submit(s,c['code'],'BUY',1,c['ask'],now,'OPTION',c)
