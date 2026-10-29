"""Audited HK quote-only SDK adapter; no trade context or order API."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from .policy import SafetyError


class HKScanQuotes:
    def __init__(self, sdk, context, evidence, clock=None):
        self.sdk, self.q = sdk, context
        self.evidence = Path(evidence)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def call(self, name, fn, *args, **kwargs):
        event = dict(query=name, started_at=self.clock().isoformat(), read_only=True)
        try:
            ret, data = fn(*args, **kwargs)
            data = data.to_dict('records') if hasattr(data, 'to_dict') else data
            event.update(return_code=ret, response=data)
        except Exception as exc:
            event.update(return_code=None, error_type=type(exc).__name__)
            raise
        finally:
            event['completed_at'] = self.clock().isoformat()
            self.evidence.parent.mkdir(parents=True, exist_ok=True)
            with self.evidence.open('a') as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str)+'\n')
                f.flush(); os.fsync(f.fileno())
        if ret != self.sdk.RET_OK:
            raise SafetyError('HK quote query failed: '+name)
        return data

    @staticmethod
    def code(code):
        if not isinstance(code, str) or len(code) != 8 or not code.startswith('HK.') or not code[3:].isdigit():
            raise SafetyError('HK canonical quote code required')
        return code

    def subscribe(self, codes):
        codes = [self.code(c) for c in codes]
        if not codes or len(codes) != len(set(codes)):
            raise SafetyError('Unique nonempty HK subscription required')
        # No US RTH session parameter. SDK's default covers HK daily candles.
        return self.call('subscribe', self.q.subscribe, codes, [self.sdk.SubType.K_DAY], subscribe_push=False)

    def unsubscribe(self, codes):
        return self.call('unsubscribe', self.q.unsubscribe,
                         [self.code(c) for c in codes], [self.sdk.SubType.K_DAY])

    def history(self, code):
        rows = self.call('daily', self.q.get_cur_kline, self.code(code), 120,
                         ktype=self.sdk.SubType.K_DAY, autype=self.sdk.AuType.QFQ)
        if not isinstance(rows, list) or any(r.get('code') != code for r in rows):
            raise SafetyError('Unexpected HK history identity')
        return rows

    def snapshot(self, code):
        rows = self.call('snapshot', self.q.get_market_snapshot, [self.code(code)])
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get('code') != code:
            raise SafetyError('Unexpected HK snapshot identity')
        return rows

    def calendar(self, start, end):
        rows = self.call('calendar', self.q.request_trading_days,
                         market=self.sdk.Market.HK, start=start, end=end)
        if not isinstance(rows, list):
            raise SafetyError('HK calendar unavailable')
        return rows
