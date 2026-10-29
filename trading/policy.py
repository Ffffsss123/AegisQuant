from dataclasses import dataclass, asdict
from datetime import date, datetime
from math import isfinite
from decimal import Decimal, ROUND_FLOOR

class SafetyError(RuntimeError):
    pass

@dataclass(frozen=True)
class Config:
    shared_capital: bool = False
    budget_usd: float = None
    loss_limit_usd: float = None
    option_cap_usd: float = None
    budget_nzd: float = 1000
    loss_limit_nzd: float = 50
    option_cap_nzd: float = 25
    allocation: float = .45
    max_positions: int = 2
    fee_reserve_usd: float = 3
    option_fee_reserve_usd: float = 5
    live_stocks: bool = False
    live_options: bool = False

    def budget(self,fx):return self.budget_usd if self.budget_usd is not None else self.budget_nzd*fx
    def loss(self,fx):return self.loss_limit_usd if self.loss_limit_usd is not None else self.loss_limit_nzd*fx
    def option_cap(self,fx):return self.option_cap_usd if self.option_cap_usd is not None else self.option_cap_nzd*fx

    def validate(self):
        for k,v in asdict(self).items():
            if isinstance(v, (float,int)) and not isinstance(v,bool) and not isfinite(v):
                raise SafetyError('Non-finite configuration')
        if any(v is not None for v in (self.budget_usd,self.loss_limit_usd,self.option_cap_usd)):
            if any(v is None for v in (self.budget_usd,self.loss_limit_usd,self.option_cap_usd)) or not 0<self.budget_usd<=890 or not 0<self.loss_limit_usd<=self.budget_usd or not 0<self.option_cap_usd<=self.budget_usd:
                raise SafetyError('Invalid explicitly configured USD budget')
        if not 0 < self.budget_nzd <= 1000 or not 0 < self.loss_limit_nzd <= 50:
            raise SafetyError('Budget/loss limit exceeds authorized limits')
        if not 0 < self.option_cap_nzd <= 25 or not 0 < self.allocation <= (1 if self.shared_capital else .45) or not 1 <= self.max_positions <= 2:
            raise SafetyError('Allocation exceeds authorized limits')
        if self.shared_capital and (self.budget_usd is None or self.option_cap_usd!=self.budget_usd):
            raise SafetyError('Shared capital requires one explicit USD pool')
        if self.fee_reserve_usd < 3 or self.option_fee_reserve_usd < 5:
            raise SafetyError('Fee reserves cannot be lowered below defaults')
        return self


def number(value, name='number'):
    try:
        v=float(value)
    except (TypeError,ValueError):
        raise SafetyError('Missing '+name)
    if not isfinite(v): raise SafetyError('Invalid '+name)
    return v


def quote_price(book, side, now, option=False, *, check_spread=True):
    bid,ask=number(book.get('bid')),number(book.get('ask'))
    if not 0 < bid <= ask: raise SafetyError('Invalid bid/ask')
    for key in ('bid_time','ask_time'):
        stamp=datetime.fromisoformat(book[key])
        if stamp.tzinfo is None: raise SafetyError('Quote timezone absent')
        age=(now-stamp).total_seconds()
        if not 0 <= age <= 30: raise SafetyError('Stale/future quote')
    if check_spread and (ask-bid)/((ask+bid)/2) > (.05 if option else .002):
        raise SafetyError('Spread too wide')
    return ask if side=='BUY' else bid


def trend(bars, expected_date):
    if len(bars)<60: raise SafetyError('Need 60 completed sessions')
    days=[b['date'] for b in bars]
    if days!=sorted(set(days)) or days[-1]!=expected_date:
        raise SafetyError('Stale, duplicate or unordered history')
    closes=[];amounts=[]
    for b in bars:
        o,h,l,c=[number(b[k],k) for k in ('open','high','low','close')]
        vol,amount=number(b['volume']),number(b['turnover'])
        if not 0<l<=min(o,c)<=max(o,c)<=h or vol<0 or amount<0:
            raise SafetyError('Invalid OHLC/volume')
        closes.append(c);amounts.append(amount)
    fast=sum(closes[-5:])/5;slow=sum(closes[-20:])/20
    liquid=closes[-1]>=5 and sum(amounts[-20:])/20>=20_000_000
    return dict(buy=fast>slow and closes[-1]>slow, sell=fast<=slow,
                liquid=liquid, strength=closes[-1]/closes[-21]-1,
                fast=fast,slow=slow,date=expected_date)


def eligible(meta):
    return (meta.get('kind') in ('COMMON','ETF_NORMAL') and meta.get('verified') is True
            and meta.get('exchange') in ('NASDAQ','NYSE','AMEX','ARCA','BATS')
            and meta.get('suspended') is False and meta.get('delisted') is False
            and meta.get('lot')==1)


def candidates(universe, histories, expected):
    selected=[];signals={};rejected={}
    for code,meta in universe.items():
        if not eligible(meta): rejected[code]='Unverified or unsupported security';continue
        try:
            s=trend(histories[code],expected);signals[code]=s
            if s['liquid'] and s['buy']: selected.append(dict(code=code,**s))
        except (SafetyError,KeyError) as exc: rejected[code]=str(exc)
    return sorted(selected,key=lambda s:(-s['strength'],s['code'])),signals,rejected


def option_candidates(contracts, signals, today, now, cap_usd, fee):
    result=[]
    for c in contracts:
        try:
            s=signals[c['underlying']]
            dte=(date.fromisoformat(c['expiry'])-today).days
            bullish=s['buy'];bearish=s['fast']<s['slow']
            if not s['liquid'] or not ((bullish and c['right']=='CALL') or (bearish and c['right']=='PUT')): continue
            if c['standard'] is not True or not 30<=dte<=60: continue
            if not .4<=abs(number(c['delta']))<=.6 or number(c['open_interest'])<1000 or number(c['volume'])<100: continue
            if c.get('snapshot_date')!=today.isoformat(): continue
            # Only verified standard 100-share contracts; adjusted contracts fail closed.
            if number(c['multiplier'])!=100: continue
            ask=quote_price(c['book'],'BUY',now,True)
            if ask*100+fee>cap_usd+1e-8: continue
            spread=(c['book']['ask']-c['book']['bid'])/((c['book']['ask']+c['book']['bid'])/2)
            result.append(dict(c,dte=dte,ask=ask,strength=abs(s['strength']),spread=spread))
        except (SafetyError,KeyError,TypeError,ValueError): continue
    return sorted(result,key=lambda c:(-c['strength'],c['spread'],abs(c['dte']-45),c['code']))


def whole_qty(cash, allocation, price, fee):
    if price<=0 or fee<0: raise SafetyError('Invalid price/fee')
    amount=max(0,min(cash,allocation)-fee)
    return int((Decimal(str(amount))/Decimal(str(price))).to_integral_value(rounding=ROUND_FLOOR))
