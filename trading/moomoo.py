"""Official SDK adapter; explicitly scoped accounts and US regular sessions."""
import socket
import time
from datetime import datetime,date,timedelta,time as walltime
from zoneinfo import ZoneInfo
from .policy import SafetyError,number

NY=ZoneInfo('America/New_York')

class Moomoo:
    def __init__(self,port=11111):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=3):pass
        except OSError as exc: raise SafetyError('OpenD 未连接：请在官方 OpenD 登录，端口 '+str(port)) from exc
        import moomoo as m
        self.m=m;self.q=None;self.t=None;self.account=None;self.env=None;self._last={};self._calendar={}
        try:
            # Quote security_firm selects crypto upstream; stock quotes use the default.
            from .quote_feed import quote_context
            self.q=quote_context(m,host='127.0.0.1',port=port)
            self._book_subscriptions=set()
            self.t=m.OpenSecTradeContext(filter_trdmarket=m.TrdMarket.US,host='127.0.0.1',port=port,security_firm=m.SecurityFirm.FUTUAU)
        except Exception:
            self.close();raise
    def now(self):return datetime.now(NY)
    def close(self):
        for ctx in (self.t,self.q):
            if ctx:ctx.close()
    def call(self,name,fn,*args,**kwargs):
        # Conservative per-interface throttling. Server quota errors are propagated, not retried in loops.
        wait=3.1-(time.monotonic()-self._last.get(name,0))
        if wait>0:time.sleep(wait)
        self._last[name]=time.monotonic()
        from .read_retry import READ_ONLY,ReadQueryTimeout,timeout_response
        try:
            if name in ('accounts','positions','orders','history_orders','cash','fees'):
                from .sdk_read_budget import spaced_read
                with spaced_read(name):ret,data=fn(*args,**kwargs)
            else:ret,data=fn(*args,**kwargs)
        except TimeoutError as exc:
            if name in READ_ONLY:raise ReadQueryTimeout(name) from exc
            raise
        if getattr(self,'audit',None) and name in ('place','cancel','orders','history_orders','fees','positions','cash'):
            raw=data.to_dict('records') if hasattr(data,'to_dict') else data
            self.audit('broker_'+name,dict(return_code=ret,response=raw))
        if ret!=self.m.RET_OK:
            if name in READ_ONLY and timeout_response(data):raise ReadQueryTimeout(name)
            raise SafetyError(str(data).split('https://')[0])
        return data
    def accounts(self):
        return self.call('accounts',self.t.get_acc_list).to_dict('records')
    def bind(self,account,env):
        if env not in ('REAL','SIMULATE'):raise SafetyError('Invalid environment')
        if self.account==str(account) and self.env==env:return
        matches=[r for r in self.accounts() if str(r['acc_id'])==str(account) and r['trd_env']==env and r['acc_status']=='ACTIVE']
        if len(matches)!=1:raise SafetyError('Explicit active account/environment not found')
        r=matches[0]
        if env=='REAL' and r['security_firm']!='FUTUAU':raise SafetyError('Wrong securities firm')
        if 'US' not in r['trdmarket_auth']:raise SafetyError('US trading not authorized')
        self.account=str(account);self.env=env
    def kw(self):
        if self.account is None:raise SafetyError('Account is not bound')
        return dict(acc_id=int(self.account),trd_env=getattr(self.m.TrdEnv,self.env))
    def settled_cash(self):
        frame=self.call('cash',self.t.accinfo_query,currency=self.m.Currency.USD,refresh_cache=True,**self.kw())
        if len(frame)!=1:raise SafetyError('Ambiguous cash response')
        r=frame.iloc[0]
        if self.env=='SIMULATE':
            # Paper accounts have no settlement/withdrawal model; never apply this fallback to REAL.
            return max(0,number(r.get('us_cash'),'paper USD cash'))
        # Conservative real-cash intersection, never fall back to margin buying power.
        cash=number(r.get('us_cash'),'USD cash')
        withdrawable=number(r.get('us_avl_withdrawal_cash'),'USD withdrawal cash')
        return max(0,min(cash,withdrawable))
    def positions(self):
        frame=self.call('positions',self.t.position_list_query,refresh_cache=True,**self.kw())
        result={}
        for r in frame.to_dict('records'):
            qty=number(r['qty']);result[r['code']]=result.get(r['code'],0)+qty
        return result
    def orders(self,start=None):
        frames=[self.call('orders',self.t.order_list_query,refresh_cache=True,**self.kw())]
        if start:
            start=max(start,(datetime.now(NY).date()-timedelta(days=89)).isoformat())
            frames.insert(0,self.call('history_orders',self.t.history_order_list_query,start=start,end=datetime.now(NY).date().isoformat(),**self.kw()))
        result={}
        for frame in frames:
            for r in frame.to_dict('records'):
                oid=str(r['order_id'])
                result[oid]=dict(id=oid,code=r['code'],side=r['trd_side'],qty=number(r['qty']),
                    filled=number(r['dealt_qty']),average=number(r['dealt_avg_price']),status=r['order_status'],tag=r.get('remark',''))
        return list(result.values())
    def fee(self,oid):
        frame=self.call('fees',self.t.order_fee_query,order_id_list=[str(oid)],**self.kw())
        match=frame[frame['order_id'].astype(str)==str(oid)]
        if len(match)!=1:raise SafetyError('Fee not yet available')
        return number(match.iloc[0]['fee_amount'])
    def place(self,code,side,qty,price,tag):
        r=self.call('place',self.t.place_order,code=code,trd_side=getattr(self.m.TrdSide,side),
            qty=qty,price=price,order_type=self.m.OrderType.NORMAL,adjust_limit=0,remark=tag,
            time_in_force=self.m.TimeInForce.DAY,fill_outside_rth=False,session=self.m.Session.RTH,**self.kw())
        if len(r)!=1:raise SafetyError('Ambiguous order acknowledgment')
        return str(r.iloc[0]['order_id'])
    def cancel(self,oid):
        return self.call('cancel',self.t.modify_order,modify_order_op=self.m.ModifyOrderOp.CANCEL,
            order_id=str(oid),qty=0,price=0,**self.kw())
    def snapshots(self,codes):
        return self.call('snapshot',self.q.get_market_snapshot,codes).to_dict('records')
    def book(self,code):
        from .quote_feed import QuoteUnavailable
        connection=self.q.book_cache.current();key=(connection,code)
        if connection is None:raise QuoteUnavailable('Quote connection interrupted','connection_interrupted')
        if key not in self._book_subscriptions:
            self.call('subscribe',self.q.subscribe,[code],[self.m.SubType.ORDER_BOOK],
                is_first_push=False,subscribe_push=True)
            self._book_subscriptions={k for k in self._book_subscriptions if k[0]==connection}
            self._book_subscriptions.add(key)
        return self.q.book_cache.get(connection,code,self.now())
    def mark(self,code,now):
        r=self.snapshots([code])[0]
        stamp=datetime.fromisoformat(r['update_time']).replace(tzinfo=NY)
        # Reconciliation and SDK throttling can outlive the loop's original timestamp.
        # Compare the source timestamp with the clock after the snapshot query, never replace it.
        now=self.now()
        if stamp>now.astimezone(NY)+timedelta(seconds=5):raise SafetyError('Future mark')
        latest=self.completed_session(now)
        if stamp.date().isoformat()<latest:raise SafetyError('Stale valuation')
        if self.execution_session(now,strict=False) and (now-stamp).total_seconds()>30:
            # A last trade may be old while the current executable bid is fresh.
            from .policy import quote_price
            book=None
            try:
                book=self.book(code)
                conservative_simulation=(getattr(self,'env',None)=='SIMULATE'
                    and getattr(self,'allow_wide_stock_valuation',False) is True)
                price=quote_price(book,'SELL',self.now(),check_spread=not conservative_simulation)
            except Exception as exc:
                if getattr(self,'audit',None):self.audit('valuation_unavailable',dict(code=code,old_trade_time=stamp.isoformat(),checked_at=self.now().isoformat(),book=book,reason=str(exc),error_type=type(exc).__name__))
                raise SafetyError('Stale intraday mark') from exc
            if getattr(self,'audit',None):self.audit('valuation_bid_fallback',dict(code=code,bid=price,bid_time=book['bid_time'],ask_time=book['ask_time'],old_trade_time=stamp.isoformat()))
            return price
        p=number(r['last_price'])
        if p<=0:raise SafetyError('Invalid mark')
        return p
    def calendar(self,now):
        key=now.astimezone(NY).date().isoformat()
        if key not in self._calendar:
            start=(date.fromisoformat(key)-timedelta(days=160)).isoformat()
            self._calendar[key]=self.call('calendar',self.q.request_trading_days,market=self.m.TradeDateMarket.US,start=start,end=key)
        return self._calendar[key]
    def session_times(self,day,kind):
        if kind not in ('WHOLE','MORNING'):raise SafetyError('Unsupported session type')
        opening=datetime.combine(date.fromisoformat(day),walltime(9,30),NY)
        closing=datetime.combine(date.fromisoformat(day),walltime(13 if kind=='MORNING' else 16),NY)
        return opening,closing
    def completed_session(self,now):
        valid=[]
        for d in self.calendar(now):
            _,close=self.session_times(d['time'],d['trade_date_type'])
            if now>=close+timedelta(minutes=5):valid.append(d['time'])
        if not valid:raise SafetyError('No completed session')
        return valid[-1]
    def execution_session(self,now,strict=True):
        now=now.astimezone(NY);day=now.date().isoformat();days=self.calendar(now)
        for i,d in enumerate(days):
            if d['time']!=day:continue
            opening,closing=self.session_times(day,d['trade_date_type'])
            end=min(opening+timedelta(minutes=10),closing) if strict else closing
            if opening+timedelta(minutes=5)<=now<end and i>0:return day,days[i-1]['time']
        return None
    def basic(self):
        rows=[]
        for kind in (self.m.SecurityType.STOCK,self.m.SecurityType.ETF):
            rows.extend(self.call('basic',self.q.get_stock_basicinfo,self.m.Market.US,kind).to_dict('records'))
        return rows
    def history(self,code,end):
        start=(date.fromisoformat(end)-timedelta(days=140)).isoformat()
        wait=3.1-(time.monotonic()-self._last.get('history',0))
        if wait>0:time.sleep(wait)
        self._last['history']=time.monotonic()
        ret,frame,key=self.q.request_history_kline(code,start=start,end=end,ktype=self.m.KLType.K_DAY,
            autype=self.m.AuType.QFQ,max_count=1000,extended_time=False)
        if ret!=self.m.RET_OK:raise SafetyError(str(frame).split('https://')[0])
        if key is not None:raise SafetyError('Unexpected history pagination')
        return [dict(date=r['time_key'][:10],**{k:r[k] for k in ('open','high','low','close','volume','turnover')}) for r in frame.to_dict('records')]
    def option_chain(self,code,today):
        frame=self.call('chain',self.q.get_option_chain,code,
            start=(today+timedelta(days=30)).isoformat(),end=(today+timedelta(days=60)).isoformat())
        return frame.to_dict('records')
    def option_data(self,chain,now):
        result=[]
        for start in range(0,len(chain),100):
            group=chain[start:start+100];snap={r['code']:r for r in self.snapshots([c['code'] for c in group])}
            for c in group:
                r=snap[c['code']]
                # Avoid subscribing to thousands of books for obviously impossible contracts.
                if c.get('option_standard_type')!='STANDARD' or c.get('suspension') is not False:continue
                try:
                    if not .4<=abs(number(r.get('option_delta')))<=.6 or number(r.get('option_open_interest'))<1000 or number(r.get('volume'))<100:continue
                    result.append(dict(code=c['code'],underlying=c['stock_owner'],expiry=c['strike_time'][:10],
                        right=c['option_type'],standard=True,multiplier=number(r.get('option_contract_multiplier')),
                        delta=r['option_delta'],open_interest=r['option_open_interest'],volume=r['volume'],
                        snapshot_date=r['update_time'][:10],book=self.book(c['code'])))
                except (SafetyError,KeyError,TypeError):continue
        return result
