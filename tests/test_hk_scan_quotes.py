import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from trading.hk_scan_quotes import HKScanQuotes
from trading.policy import SafetyError

class HKScanQuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'evidence.jsonl'
        self.sdk=SimpleNamespace(RET_OK=0,SubType=SimpleNamespace(K_DAY='K_DAY'),AuType=SimpleNamespace(QFQ='QFQ'),Market=SimpleNamespace(HK='HK'))
        self.q=Mock();self.b=HKScanQuotes(self.sdk,self.q,self.file)
    def test_subscribe_never_sets_us_session(self):
        self.q.subscribe.return_value=(0,None);self.b.subscribe(['HK.00001'])
        self.q.subscribe.assert_called_once_with(['HK.00001'],['K_DAY'],subscribe_push=False)
    def test_history_uses_free_current_candle_api(self):
        self.q.get_cur_kline.return_value=(0,[{'code':'HK.00001'}])
        self.b.history('HK.00001')
        self.q.get_cur_kline.assert_called_once_with('HK.00001',120,ktype='K_DAY',autype='QFQ')
    def test_other_market_rejected_before_sdk(self):
        with self.assertRaises(SafetyError):self.b.snapshot('US.AA')
        self.q.get_market_snapshot.assert_not_called()
    def test_failed_response_is_durable(self):
        self.q.get_market_snapshot.return_value=(-1,'no permission')
        with self.assertRaises(SafetyError):self.b.snapshot('HK.00001')
        self.assertEqual(json.loads(self.file.read_text())['return_code'],-1)
    def test_exception_is_durable(self):
        self.q.get_cur_kline.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):self.b.history('HK.00001')
        self.assertEqual(json.loads(self.file.read_text())['error_type'],'TimeoutError')
