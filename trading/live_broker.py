"""Explicit REAL stock adapter; simulation adapters remain unchanged."""
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from .moomoo import Moomoo, NY
from .policy import SafetyError, number, quote_price
from .hk_policy import HK, execution_session


class LiveBroker(Moomoo):
    def __init__(self, market, account, port=11111, firm='FUTUAU'):
        if market not in ('US', 'HK') or not str(account).isdigit():
            raise SafetyError('Explicit stock market and REAL account required')
        # Do not change or reuse the simulation adapter's trading context.
        import moomoo as sdk
        from .quote_feed import quote_context
        self.market=market; self.timezone=NY if market=='US' else HK
        self.m=sdk; self.q=self.t=None; self.account=self.env=None
        self._last={}; self._calendar={}; self._book_subscriptions=set()
        if firm not in ('FUTUAU','FUTUINC','FUTUSECURITIES','FUTUSG'):
            raise SafetyError('Unsupported securities entity')
        self.firm=firm
        try:
            self.q=quote_context(sdk,host='127.0.0.1',port=port)
            self.t=sdk.OpenSecTradeContext(filter_trdmarket=getattr(sdk.TrdMarket,market),
                host='127.0.0.1',port=port,security_firm=getattr(sdk.SecurityFirm,firm))
            self.bind(account,'REAL')
        except Exception:self.close();raise

    def now(self):return datetime.now(self.timezone)

    def bind(self,account,env):
        if env!='REAL':raise SafetyError('Live adapter cannot access simulation accounts')
        if self.account==str(account) and self.env==env:return
        rows=[r for r in self.accounts() if str(r.get('acc_id'))==str(account)
              and r.get('trd_env')=='REAL' and r.get('acc_status')=='ACTIVE'
              and r.get('security_firm')==self.firm and self.market in r.get('trdmarket_auth',[])]
        if len(rows)!=1:raise SafetyError('Unique active REAL account and market permission required')
        self.account=str(account);self.env=env

    def settled_cash(self):
        currency=self.m.Currency.USD if self.market=='US' else self.m.Currency.HKD
        rows=self.call('cash',self.t.accinfo_query,currency=currency,refresh_cache=True,**self.kw()).to_dict('records')
        if len(rows)!=1:raise SafetyError('Ambiguous live cash response')
        r=rows[0];prefix='us' if self.market=='US' else 'hk'
        cash=number(r.get(prefix+'_cash'));available=number(r.get(prefix+'_avl_withdrawal_cash'))
        power=number(r.get('usd_net_cash_power' if self.market=='US' else 'hkd_net_cash_power'))
        if min(cash,available,power)<0:raise SafetyError('Negative live cash or debt')
        return min(cash,available,power)  # Never use margin buying power or automatic FX.

    def positions(self):
        return {c:q for c,q in super().positions().items() if c.startswith(self.market+'.')}

    def orders(self,start=None):
        frames=[self.call('orders',self.t.order_list_query,refresh_cache=True,**self.kw())]
        if start:
            today=self.now().date();start=max(start,(today-timedelta(days=89)).isoformat())
            frames.insert(0,self.call('history_orders',self.t.history_order_list_query,start=start,end=today.isoformat(),**self.kw()))
        rows={}
        for frame in frames:
            for r in frame.to_dict('records'):
                if not r['code'].startswith(self.market+'.'):continue
                oid=str(r['order_id']);rows[oid]=dict(id=oid,code=r['code'],side=r['trd_side'],
                    qty=number(r['qty']),filled=number(r['dealt_qty']),average=number(r['dealt_avg_price']),
                    status=r['order_status'],tag=r.get('remark',''))
        return list(rows.values())

    def call(self,name,fn,*args,**kwargs):
        result=super().call(name,fn,*args,**kwargs)
        if getattr(self,'audit',None) and name in ('place','cancel','orders','history_orders','positions','fees','cash'):
            self.audit('live_broker_response',dict(market=self.market,env='REAL',interface=name,
                response=result.to_dict('records') if hasattr(result,'to_dict') else result))
        return result

    def calendar(self,now):
        day=now.astimezone(self.timezone).date().isoformat()
        if day not in self._calendar:
            start=(date.fromisoformat(day)-timedelta(days=160)).isoformat()
            self._calendar[day]=self.call('calendar',self.q.request_trading_days,
                market=getattr(self.m.TradeDateMarket,self.market),start=start,end=day)
        return self._calendar[day]

    def session_times(self,day,kind):
        if self.market=='US':return super().session_times(day,kind)
        if kind not in ('WHOLE','MORNING'):raise SafetyError('Unknown HK session')
        return (datetime.combine(date.fromisoformat(day),time(9,30),HK),
                datetime.combine(date.fromisoformat(day),time(12 if kind=='MORNING' else 16),HK))

    def completed_session(self,now):
        rows=[r['time'] for r in self.calendar(now)
              if now>=self.session_times(r['time'],r['trade_date_type'])[1]+timedelta(minutes=5)]
        if not rows:raise SafetyError('Completed live market session missing')
        return rows[-1]

    def execution_session(self,now,strict=True):
        if self.market=='US':return super().execution_session(now,strict)
        rows=self.calendar(now);day=execution_session(rows,now)
        previous=[r['time'] for r in rows if day and r['time']<day]
        return (day,previous[-1]) if previous else None

    def stock_metadata(self,code,qty,price):
        if not code.startswith(self.market+'.'):raise SafetyError('Wrong stock market')
        rows=self.call('basic_live',self.q.get_stock_basicinfo,
            market=getattr(self.m.Market,self.market),stock_type=self.m.SecurityType.STOCK,
            code_list=[code]).to_dict('records')
        snap=self.snapshots([code])
        if len(rows)!=1 or len(snap)!=1 or rows[0].get('code')!=code or snap[0].get('code')!=code:
            raise SafetyError('Unique stock identity required')
        row=rows[0];status=snap[0]
        if row.get('delisting') is not False or status.get('suspension') is not False:
            raise SafetyError('Stock status unverified or suspended')
        lot=number(row.get('lot_size'))
        if lot<=0 or lot!=int(lot) or qty%lot:raise SafetyError('Whole verified stock lots required')
        if self.market=='US':
            if lot!=1 or round(price,2)!=price:raise SafetyError('US whole shares and cent limit prices required')
        return dict(lot=int(lot),snapshot=status,checked_at=self.now().isoformat())

    def mark(self,code,now):
        # Preserve the original REAL valuation rule outside execution sessions;
        # executable orders still always require a fresh, narrow two-sided book.
        now=self.now()
        if self.execution_session(now,strict=False):
            return quote_price(self.book(code),'SELL',self.now())
        rows=self.snapshots([code])
        if len(rows)!=1 or rows[0].get('code')!=code:raise SafetyError('Unique live valuation required')
        row=rows[0];stamp=datetime.fromisoformat(row['update_time'])
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=self.timezone)
        if stamp>now+timedelta(seconds=5) or stamp.astimezone(self.timezone).date().isoformat()<self.completed_session(now):
            raise SafetyError('Stale/future closed-session live valuation')
        value=number(row['last_price'])
        if value<=0:raise SafetyError('Invalid live valuation')
        return value

    def place(self,code,side,qty,price,tag):
        # The engine has already durably recorded intent. Never change its limit.
        current=quote_price(self.book(code),side,self.now())
        if (side=='BUY' and current>price) or (side=='SELL' and current<price):
            raise SafetyError('Quote moved beyond fixed limit; intent retained for reconciliation')
        if getattr(self,'before_live_send',None):self.before_live_send(side)
        args=dict(code=code,trd_side=getattr(self.m.TrdSide,side),qty=qty,price=price,
            order_type=self.m.OrderType.NORMAL,adjust_limit=0,remark=tag,
            time_in_force=self.m.TimeInForce.DAY,fill_outside_rth=False,**self.kw())
        args['session']=self.m.Session.RTH if self.market=='US' else self.m.Session.NONE
        frame=self.call('place',self.t.place_order,**args)
        if len(frame)!=1:raise SafetyError('Ambiguous live acknowledgment; query before any retry')
        return str(frame.iloc[0]['order_id'])

    def book(self,code):
        # A new short-lived ticket session needs its first asynchronous push.
        # Wait on the cache only; never substitute an untimestamped snapshot.
        import time as monotonic_time
        from .quote_feed import QuoteUnavailable
        deadline=monotonic_time.monotonic()+3
        while True:
            try:return super().book(code)
            except QuoteUnavailable:
                if monotonic_time.monotonic()>=deadline:raise
                monotonic_time.sleep(.1)
