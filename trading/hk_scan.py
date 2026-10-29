"""Single-process HK quote scan. No trade context, account binding or orders."""
import fcntl
import json
import time
from pathlib import Path
from datetime import date, datetime, time as daytime, timedelta, timezone
from urllib.request import urlopen
from .hk_policy import HK
from .policy import SafetyError
from .hk_classification import reviewed_class
from .hk_scan_quotes import HKScanQuotes
from .hk_collect import collect
from .hk_fx import ECB, parse_ecb
from .rolling_scan import atomic
from .validation import protection_passed


def completed_session(calendar, now):
    if now.tzinfo is None:
        raise SafetyError('Aware HK scan clock required')
    days = [r['time'] for r in calendar]
    if days != sorted(set(days)):
        raise SafetyError('Invalid HK calendar order')
    completed = []
    for row in calendar:
        kind = row.get('trade_date_type')
        if kind not in ('WHOLE', 'MORNING'):
            raise SafetyError('Unsupported HK session')
        close = daytime(16, 15) if kind == 'WHOLE' else daytime(12, 15)
        if datetime.combine(date.fromisoformat(row['time']), close, HK) <= now:
            completed.append(row['time'])
    if not completed:
        raise SafetyError('No completed HK trading session')
    return completed[-1]


def main():
    import moomoo as sdk
    from .desktop_news import hk_claimed_codes
    root = Path(__file__).resolve().parents[1]
    data = root/'data/trading'
    market = data/'hk_market'
    if not protection_passed(root):
        raise SafetyError('Protection validation required')
    market.mkdir(parents=True, exist_ok=True)
    with (market/'scan.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(timezone.utc)
        catalog = json.loads((market/'catalog.json').read_text())
        if catalog['source_date'] != now.astimezone(HK).date().isoformat():
            raise SafetyError('Current HK directory required')
        # Include all claimed symbols conservatively, even closed history.
        held = hk_claimed_codes(data/'hk_submissions.sqlite')
        selected = set(held)
        for code, metadata in catalog['rows'].items():
            try:
                reviewed_class(market, code, metadata, now)
                selected.add(code)
            except (SafetyError, KeyError, ValueError, TypeError, OSError):
                continue
        with urlopen(ECB, timeout=25) as response:
            if response.url != ECB:
                raise SafetyError('Unexpected FX source redirect')
            fx = parse_ecb(response.read(100001), now)
        atomic(data/'hk_fx_reference.json', fx)
        q = sdk.OpenQuoteContext(host='127.0.0.1', port=11111)
        subscribed = None
        broker = HKScanQuotes(sdk, q, market/'scan_quote_evidence.jsonl')
        try:
            today = now.astimezone(HK).date()
            end = (today+timedelta(days=45)).isoformat()
            calendar = broker.calendar((today-timedelta(days=370)).isoformat(), end)
            target = completed_session(calendar, now)
            atomic(market/'scan_calendar.json', dict(rows=calendar, queried_at=now.isoformat(), range_end=end))
            if selected:
                quota = broker.call('subscription_quota', q.query_subscription, False)
                if not isinstance(quota, dict) or int(quota.get('remain', 0))-20 < len(selected):
                    raise SafetyError('Insufficient HK subscription room; existing subscriptions preserved')
                broker.subscribe(sorted(selected)); subscribed = time.monotonic()
            result = collect(market, catalog, calendar, target, fx, now,
                             broker.history, broker.snapshot, held=held)
            print(json.dumps(dict(date=target, complete=result['complete'],
                eligible=result['eligible_symbols'], acquired=result['acquired_symbols'],
                candidates=len(result['candidates']), errors=result['errors'], orders_enabled=False)))
            if not result['complete']:
                raise SafetyError('HK scan incomplete; prior result preserved')
        finally:
            if subscribed is not None:
                # OpenD subscriptions cannot be cancelled in their first minute.
                while time.monotonic()-subscribed < 61:
                    time.sleep(min(5, 61-(time.monotonic()-subscribed)))
                try:
                    broker.unsubscribe(sorted(selected))
                finally:
                    q.close()
            else:
                q.close()


if __name__ == '__main__':
    main()
