"""SIMULATE-only short swing strategy on top of the existing order protections."""
from datetime import datetime,timedelta,timezone
from .engine import Engine
from .policy import SafetyError,quote_price
from .moomoo import NY
from .swing_model import VERSION,PARAMETERS,size,exit_reason

class SwingEngine(Engine):
    def sell_without_mark(self,s,item):
        return (s.get('env')=='SIMULATE' and item.get('kind')=='STOCK' and
                item.get('side')=='SELL' and item.get('exit_reason') in
                ('stop','target','max_sessions','trend_reversal','emergency_news'))
    def execution_quote(self,s,item,book,now):
        if not self.sell_without_mark(s,item):return super().execution_quote(s,item,book,now)
        bid=quote_price(book,'SELL',now,check_spread=False)
        if item['exit_reason']=='target':
            from .policy import number
            target=number(s['positions'][item['code']].get('swing_target'),'target')
            if target<=0:raise SafetyError('Invalid target')
            return max(bid,target)
        return bid

    @staticmethod
    def temporary_entry_active(s,now):
        rule=s.get("temporary_entry_window",{})
        if s.get("env")!="SIMULATE" or rule.get("session")!=now.astimezone(NY).date().isoformat():return False
        try:
            start=datetime.fromisoformat(rule["starts_at"]);end=datetime.fromisoformat(rule["expires_at"])
            return start.tzinfo is not None and end.tzinfo is not None and start<=now<end
        except (KeyError,ValueError,TypeError):return False
    def entry_session(self,s,now):
        continuous=s.get('env')=='SIMULATE' and s.get('entry_timing')=='regular_session'
        return self.b.execution_session(now,strict=not (continuous or self.temporary_entry_active(s,now)))
    def quote_waitable(self,s,item,exc):
        # No broker intent exists yet: retain the original exit during bounded quote wait.
        return (super().quote_waitable(s,item,exc) or
            (s.get('env')=='SIMULATE' and item.get('kind')=='STOCK' and
             item.get('side')=='SELL' and str(exc)=='Spread too wide'))
    def load(self):
        s=super().load()
        if s.get('env')!='SIMULATE' or s.get('strategy_version')!=VERSION:raise SafetyError('Short swing requires its explicit simulation binding')
        return s
    def reconcile(self,s,now):
        super().reconcile(s,now)
        changed=False
        for o in s['orders'].values():
            plan=o.get('stock_plan');p=s['positions'].get(o['code'])
            if o['side']!='BUY' or not plan or not o['booked_qty'] or not p or not p['qty']:continue
            avg=o['booked_notional']/o['booked_qty']
            values=dict(swing_entry=avg,swing_stop=avg-plan['stop_distance'],swing_target=avg+plan['target_distance'],
                        entry_session=datetime.fromisoformat(o['created']).astimezone(NY).date().isoformat())
            if any(p.get(k)!=v for k,v in values.items()):p.update(values);changed=True
        if changed:self.store.save(s,'swing_fill_levels')
        return s
    def independent_sizing(self,s):
        binding=getattr(self,'_independent_sizing',None)
        if (s.get('env')!='SIMULATE' or s.get('capital_mode')!='independent_sleeves_v1'
                or s.get('sleeve')!='US_STOCK' or not binding or binding[0] is not s
                or not 0 <= (datetime.now(timezone.utc)-binding[2]).total_seconds() <= 30):
            raise SafetyError('Fresh lock-held independent sizing required')
        return binding[1]
    def combined_equity(self,s,now):
        if s.get('capital_mode') is not None:
            # risk_room subtracts this from the stock baseline: only combined
            # execution loss matters, never quote-only observer profits.
            return s['initial']-float(self.independent_sizing(s)['combined_loss'])
        equity=s['marks'][-1]['equity_usd'] if s.get('marks') else s['initial']
        from .hk_shared_admission import exposure
        equity+=exposure(s,now)['total_net_pnl_usd']
        if not s.get('option_budget_usd'):return equity
        o=self.option_state() if hasattr(self,'option_state') else {}
        if o.get('env')!='QUOTE_PAPER' or o.get('portfolio_id')!=s.get('portfolio_id') or o.get('initial')!=s['option_budget_usd']:
            raise SafetyError('期权研究额度或组合身份不一致，暂停新买入')
        if o.get('position'):
            if not o.get('valuation_valid') or not o.get('marks'):raise SafetyError('期权研究持仓缺少有效估值')
            if not 0<=(now-datetime.fromisoformat(o['marks'][-1]['time'])).total_seconds()<=30:raise SafetyError('期权研究持仓估值过期')
            value=o['marks'][-1]['equity_usd']
        else:value=o['cash']
        return equity+value-o['initial']
    def stock_cash(self,s):
        if s.get('capital_mode') is not None:
            return max(0,min(float(self.independent_sizing(s)['available_usd']),self.b.settled_cash()))
        if s.get('shared_capital'):
            from .shared_capital import available_cash
            return max(0,min(available_cash(s,self.option_state()),self.b.settled_cash())-self.c.fee_reserve_usd)
        return max(0,min(s['cash'],self.b.settled_cash())-s.get('option_budget_usd',0))
    def risk_room(self,s):
        now=self.clock(datetime.fromisoformat(s['marks'][-1]['time'])) if s.get('marks') else datetime.now().astimezone()
        equity=self.combined_equity(s,now)
        used=0
        for p in s['positions'].values():
            if p['qty']:
                if 'swing_stop' not in p:raise SafetyError('Missing position risk levels')
                used+=p['qty']*(max(0,p['swing_entry']-p['swing_stop'])+p['swing_entry']*.003)+2*self.c.fee_reserve_usd
        return max(0,s['loss_limit']-max(0,s['initial']-equity)-used)
    def order_quantity(self,s,item,price,factor):
        if item.get('qty'):return item['qty']
        sig=self.active_scan['signals'][item['code']]
        return size(self.stock_cash(s),s['initial'],price,sig,self.c.fee_reserve_usd,self.c.allocation*factor,self.risk_room(s))
    def entry_plan(self,s,code,qty,price):
        sig=self.active_scan['signals'][code]
        factor=s.get('news_status',{}).get(code,{}).get('multiplier',0)
        allowed=size(self.stock_cash(s),s['initial'],price,sig,self.c.fee_reserve_usd,self.c.allocation*factor,self.risk_room(s))
        if qty>allowed:raise SafetyError('Short-swing risk or round-trip cost budget exceeded')
        return dict(version=VERSION,stop_distance=sig['stop_distance'],target_distance=sig['target_distance'])
    def tick(self,scan,now):
        self.active_scan=scan
        s=self.load();self.reconcile(s,now)
        try:self.mark(s,now)
        except Exception as exc:self.alert(s,'Valuation unavailable: '+str(exc))
        urgent=s.setdefault('emergency_exits',{})
        for code,p in s['positions'].items():
            if not p['qty']:
                urgent.pop(code,None);continue
            if code not in urgent:
                decision=self.news(code,self.clock(now))
                if decision.get('exit_long'):
                    urgent[code]=dict(detected_at=self.clock(now).isoformat(),evidence=decision.get('exit_evidence',[]))
                    self.store.save(s,'emergency_news_exit_latched',dict(code=code,**urgent[code]))
        risk_block=None
        try:
            combined=self.combined_equity(s,self.clock(now));s['combined_risk_equity_usd']=combined
            if s['initial']-combined>=s['loss_limit']:self.alert(s,'股票与期权研究合并亏损达到上限，暂停新买入')
        except SafetyError as exc:risk_block=str(exc)
        self.manage_orders(s,now,reconciled=True)
        if s['stopped'] or self.blocked_orders(s):return s
        session=self.b.execution_session(now,strict=False)
        if not session:return s
        day,previous=session
        current=scan.get('date')==previous and scan.get('version')==VERSION
        s['data_block']=None if current and scan.get('complete') else '等待短线策略的上一交易日完整信号'
        if s.get('queue_day')!=day:
            s['queue']=[];s['queue_day']=day;s.pop('quote_wait',None)
        calendar=self.b.calendar(now);dates=[r['time'] for r in calendar]
        row=next(r for r in calendar if r['time']==day)
        _,close=self.b.session_times(day,row['trade_date_type']);near_close=now>=close-timedelta(minutes=15)
        claims=s.setdefault('swing_exit_claims',[])
        s['exit_quote_status']={c:v for c,v in s.get('exit_quote_status',{}).items() if s['positions'].get(c,{}).get('qty')}
        missing=[c for c,p in s['positions'].items() if p['qty'] and (not current or c not in scan.get('signals',{}))]
        if missing:s['data_block']='持仓趋势数据缺失：'+', '.join(missing)
        for code,p in s['positions'].items():
            if not p['qty']:continue
            emergency=urgent.get(code)
            if not emergency and ('entry_session' not in p or p['entry_session'] not in dates):
                self.alert(s,'Missing short-swing entry session: '+code);continue
            count=dates.index(day)-dates.index(p['entry_session'])+1 if p.get('entry_session') in dates else 0
            p['holding_sessions']=count
            if count>5:self.alert(s,'Maximum holding window exceeded; exit needs attention: '+code)
            key=day+':'+code
            if key in claims:continue
            # Time/trend exits can queue even while waiting for a quote; drain enforces price safety.
            reason='emergency_news' if emergency else 'max_sessions' if count>=5 and near_close else 'trend_reversal' if current and scan.get('signals',{}).get(code,{}).get('sell') else None
            if reason is None:
                try:
                    book=self.b.book(code);bid=quote_price(book,'SELL',self.clock(now),check_spread=False)
                    reason=exit_reason(p,None,bid,count,near_close)
                    evaluation=dict(reason=reason,wide_spread=(book['ask']-bid)/((book['ask']+bid)/2)>.002)
                    previous=s.setdefault('exit_evaluations',{}).get(code)
                    if previous!=evaluation:
                        self.store.event('swing_exit_evaluation',dict(code=code,**evaluation,stop=p.get('swing_stop'),target=p.get('swing_target'),book=book))
                        s['exit_evaluations'][code]=evaluation
                    s.setdefault('exit_quote_status',{}).pop(code,None)
                except (SafetyError,KeyError,ValueError) as exc:
                    s.setdefault('exit_quote_status',{})[code]=str(exc);continue
            if reason:
                s['exit_quote_status'].pop(code,None)
                claims.append(key);s['queue']=[i for i in s['queue'] if not (i['code']==code and i['side']=='SELL')]
                s['queue'].insert(0,dict(code=code,side='SELL',kind='STOCK',qty=p['qty'],exit_reason=reason,news_evidence=emergency))
        if risk_block:s['data_block']=risk_block
        if s.get('exit_quote_status'):s['data_block']='持仓退出报价不可验证，暂停新买入'
        if self.entry_session(s,now) and day not in s['processed']:
            if not s['paused'] and not s['data_block'] and (day>s['entry_after_session'] or self.temporary_entry_active(s,now)):
                s['processed'].append(day)
                count=sum(p['qty']>0 for p in s['positions'].values())
                from .candidate_fallback import ranked,LIMIT
                selected=ranked(scan,s,day,LIMIT if s.get('candidate_fallback_enabled') and count<self.c.max_positions else max(0,self.c.max_positions-count))
                s['queue'] += [dict(code=c['code'],side='BUY',kind='STOCK') for c in selected]
        self.store.save(s,'swing_decisions')
        return s
    def drain(self,scan,now):
        self.active_scan=scan
        s=self.load()
        if not self.entry_session(s,now):
            if any(i['side']=='BUY' for i in s.get('queue',[])):
                s['queue']=[i for i in s['queue'] if i['side']!='BUY'];s.pop('quote_wait',None)
                self.store.save(s,'swing_entry_window_closed')
        item=(s.get('queue') or [None])[0]
        if s.get('candidate_fallback_enabled') and item and item['side']=='BUY':
            # Pending orders must be serviced even when all new entries are blocked.
            if self.blocked_orders(s):
                self.manage_orders(s,now);return
            if s['paused'] or s['stopped']:return
            count=sum(p['qty']>0 and p['kind']=='STOCK' for p in s['positions'].values())
            if count>=self.c.max_positions:
                s['queue']=[i for i in s['queue'] if i['side']!='BUY']
                self.store.save(s,'candidate_capacity_reached');return
            decision=self.news(item['code'],self.clock(now))
            if decision.get('global_block'):
                s['entry_global_block']=decision
                self.store.save(s,'candidate_global_news_block',decision);return
            s.pop('entry_global_block',None)
            self.store.save(s,'candidate_global_news_ready')
        result=super().drain(scan,now)
        s=self.load()
        if s.get('candidate_fallback_enabled') and item and item['side']=='BUY' and item not in s.get('queue',[]):
            key=s.get('queue_day','')+':'+item['code']
            row=dict(code=item['code'],time=now.isoformat(),result=result or {'status':'submitted_or_blocked'})
            s.setdefault('candidate_checks',{})[key]=row
            if result and result.get('status')=='skipped' and not result.get('replaceable'):
                self.alert(s,'Candidate execution error; alternatives blocked: '+result.get('reason','unknown'))
            self.store.save(s,'candidate_checked',row)
        # A failed exit is observable and blocks new entries; no same-day repricing loop.
        for code,p in s['positions'].items():
            if not p['qty']:continue
            key=now.astimezone(NY).date().isoformat()+':'+code
            queued=any(i['code']==code and i['side']=='SELL' for i in s.get('queue',[]))
            if key in s.get('swing_exit_claims',[]) and not queued and not self.blocked_orders(s):
                self.alert(s,'Short-swing exit unfilled; inspect simulation position: '+code)


def adopt_empty_simulation(b,db,probe,scan,now):
    """Explicit version change only for a flat, unused strategy ledger; retain all history."""
    from .engine import TERMINAL
    s=db.load()
    if s.get('env')!='SIMULATE' or s.get('purpose')!='strategy_simulation' or s.get('account')!=probe.get('account'):
        raise SafetyError('Simulation identity mismatch')
    if s.get('strategy_version')==VERSION:return s
    if s.get('strategy_version') not in (None,'daily_sma_v1') or s['orders'] or any(p['qty'] for p in s['positions'].values()) or any(s['baseline'].values()):
        raise SafetyError('Existing strategy activity requires a separate migration review')
    if not scan.get('complete') or scan.get('version')!=VERSION:raise SafetyError('Short-swing signals incomplete')
    if s.get('enabled_stocks') or s.get('enabled_options'):raise SafetyError('Product switches must remain disabled')
    b.bind(s['account'],'SIMULATE');remote=b.orders(s['bound_at'][:10])
    known={o['id'] for o in probe['orders'].values() if o.get('id')}
    if any(b.positions().values()) or any(o['status'] not in TERMINAL or o['id'] not in known for o in remote):
        raise SafetyError('Unknown broker activity prevents strategy migration')
    s.update(strategy_version=VERSION,strategy_parameters=PARAMETERS,strategy_acceptance=False)
    s.setdefault('strategy_migrations',[]).append(dict(time=now.isoformat(),previous='daily_sma_v1',version=VERSION))
    db.save(s,'strategy_version_migration',dict(version=VERSION,remote_orders_checked=len(remote)))
    return s
