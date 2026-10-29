import unittest
from datetime import datetime, timezone
from trading.position_quotes import quote_row, display_quote

class PositionQuotes(unittest.TestCase):
    def test_source_timestamp_and_market_timezone(self):
        for code, expected in [('HK.00700', '2026-09-17T02:00:00+00:00'), ('US.AAPL', '2026-09-17T14:00:00+00:00')]:
            row=quote_row(dict(code=code,last_price=10,update_time='2026-09-17 10:00:00'))
            self.assertEqual(row['time'],expected)
    def test_missing_timestamp_or_invalid_price_not_fabricated(self):
        for row in [dict(code='US.A',last_price=10),dict(code='US.A',last_price='NaN',update_time='2026-09-17 10:00:00')]:
            self.assertIsNone(quote_row(row))
    def test_stale_and_failed_quote_remain_explicit(self):
        cache=dict(quotes={'US.A':dict(price=10,time='2026-09-17T10:00:00+00:00')},error='offline')
        result=display_quote(cache,'US.A',datetime(2026,9,17,11,tzinfo=timezone.utc))
        self.assertEqual(result['current_price'],10)
        self.assertIn('超过 30 秒',result['price_status']);self.assertIn('暂不可用',result['price_status'])
    def test_future_or_naive_quote_not_shown(self):
        for stamp in ['2026-09-17T12:00:00+00:00','2026-09-17T10:00:00']:
            result=display_quote(dict(quotes={'US.A':dict(price=10,time=stamp)}),'US.A',datetime(2026,9,17,11,tzinfo=timezone.utc))
            self.assertIsNone(result['current_price'])
