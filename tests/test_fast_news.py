import tempfile,unittest
from pathlib import Path
from datetime import timedelta
from urllib.error import HTTPError
from unittest.mock import Mock
from trading.news import coverage_age_limit,assess,POLL_SECONDS
from trading.news_backoff import fetch_with_backoff,FeedBackoff
from tests.test_news import snapshot
from tests.test_trading import NOW
class FasterNews(unittest.TestCase):
 def test_two_minute_cycle_and_three_minute_freshness(self):
  self.assertEqual(POLL_SECONDS,120);s=snapshot();s['poll_interval_seconds']=120
  self.assertEqual(coverage_age_limit(s),180);self.assertEqual(assess(s,'US.A',NOW+timedelta(seconds=180))['multiplier'],1)
  self.assertEqual(assess(s,'US.A',NOW+timedelta(seconds=181))['multiplier'],0)
 def test_old_failed_and_future_health_still_block(self):
  s=snapshot();s['poll_interval_seconds']=120;s['health']['US.A']['ok']=False
  self.assertEqual(assess(s,'US.A',NOW)['multiplier'],0)
  self.assertEqual(assess(s,'US.A',NOW-timedelta(seconds=1))['multiplier'],0)
 def test_rate_limit_survives_restart_without_requery(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'backoff.json';spec=dict(id='test',url='https://example.com/rss');fetch=Mock(side_effect=HTTPError(spec['url'],429,'rate limited',{'Retry-After':'600'},None))
   with self.assertRaises(FeedBackoff) as r:fetch_with_backoff(spec,fetch,path,NOW)
   self.assertEqual(r.exception.retry_at,(NOW+timedelta(seconds=600)).isoformat())
   with self.assertRaises(FeedBackoff):fetch_with_backoff(spec,fetch,path,NOW+timedelta(seconds=120))
   self.assertEqual(fetch.call_count,1)
   good=Mock(return_value=[]);self.assertEqual(fetch_with_backoff(spec,good,path,NOW+timedelta(seconds=601)),[]);good.assert_called_once()
 def test_retry_after_http_date_and_failed_source_not_success(self):
  with tempfile.TemporaryDirectory() as root:
   fetch=Mock(side_effect=HTTPError('https://example.com/rss',503,'busy',{'Retry-After':'Tue, 08 Sep 2026 14:35:00 GMT'},None))
   with self.assertRaises(FeedBackoff) as r:fetch_with_backoff(dict(url='https://example.com/rss'),fetch,Path(root)/'state',NOW)
   self.assertEqual(r.exception.retry_at,(NOW+timedelta(hours=1)).isoformat())
