"""Read-only display quotes; independent of strategy order and risk decisions."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .position_display import positive


def quote_row(row):
    code = row.get('code', '')
    price = positive(row.get('last_price'))
    zone = {'US': 'America/New_York', 'HK': 'Asia/Hong_Kong'}.get(code.split('.')[0])
    if not price or not zone:
        return None
    try:
        stamp = datetime.fromisoformat(row['update_time'])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo(zone))
    except (KeyError, TypeError, ValueError):
        return None
    return dict(price=price, time=stamp.astimezone(timezone.utc).isoformat())


def display_quote(cache, code, now=None):
    now = now or datetime.now(timezone.utc)
    row = cache.get('quotes', {}).get(code, {})
    price = positive(row.get('price'))
    try:
        stamp = datetime.fromisoformat(row['time'])
        age = (now-stamp).total_seconds()
        if not price or stamp.tzinfo is None or age < -5:
            raise ValueError('Invalid quote')
    except (KeyError, TypeError, ValueError):
        return dict(current_price=None, price_status='暂无有效报价', price_time=None)
    status = '最新成交价' if age <= 30 else '最近成交价 · 已超过 30 秒'
    if cache.get('error'):
        status += ' · 更新暂不可用'
    return dict(current_price=price, price_status=status, price_time=row['time'])


def supervise(stop, root, mode=lambda: 'simulation'):
    """One quote-only connection, snapshots every 30s; no trade context exists."""
    from .desktop_server import ledger, read_json
    from .rolling_scan import atomic
    import moomoo as m
    path = root/'data/desktop/position_quotes.json'
    context = None
    try:
        while not stop.is_set():
            try:
                codes = set();simulation=mode()=='simulation'
                for name in (('paper.sqlite', 'probe.sqlite', 'option_transport.sqlite') if simulation else ()):
                    state = ledger(root/'data/trading'/name)
                    codes.update(c for c,p in state.get('positions', {}).items() if p.get('qty'))
                external = read_json(root/'data/desktop/account.json')
                if not simulation:
                    codes.update(p['code'] for a in external.get('accounts', []) for p in a.get('positions', []) if p.get('qty'))
                    for market in ('us','hk'):
                        state=ledger(root/('data/trading/live/'+market+'.sqlite'))
                        codes.update(c for c,p in state.get('positions',{}).items() if p.get('qty'))
                option = read_json(root/'data/trading/options.report.json').get('position')
                if option and simulation:
                    codes.add(option['code'])
                if codes:
                    if context is None:
                        context = m.OpenQuoteContext(host='127.0.0.1', port=11111)
                    ret, rows = context.get_market_snapshot(sorted(codes))
                    if ret != m.RET_OK:
                        raise RuntimeError('Quote query failed')
                    quotes = {}
                    for row in rows.to_dict('records'):
                        parsed = quote_row(row)
                        if parsed and row.get('code') in codes:
                            quotes[row['code']] = parsed
                    atomic(path, dict(quotes=quotes, checked_at=datetime.now(timezone.utc).isoformat()))
            except Exception:
                cached = read_json(path)
                cached['error'] = 'Quote refresh unavailable'
                atomic(path, cached)
                if context is not None:
                    context.close()
                    context = None
            stop.wait(30)
    finally:
        if context is not None:
            context.close()
