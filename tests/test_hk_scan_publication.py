import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from trading.hk_scan_publication import publish
from trading.policy import SafetyError


class HKScanPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 9, 18, tzinfo=timezone.utc)
        self.result = dict(date='2026-09-17', complete=True, errors={},
            orders_enabled=False, candidates=[], signals={}, scope='Reviewed share classes only')

    def send(self):
        return publish(self.root, self.result, self.result['date'], self.now)

    def test_complete_cash_result_published(self):
        self.send()
        self.assertEqual(json.loads((self.root/'scan.json').read_text())['candidates'], [])
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_partial_preserves_previous(self):
        self.send(); old = (self.root/'scan.json').read_bytes()
        self.result.update(complete=False, errors={'HK.00001':'missing data'})
        with self.assertRaises(SafetyError): self.send()
        self.assertEqual((self.root/'scan.json').read_bytes(), old)

    def test_older_run_cannot_overwrite_newer(self):
        self.send(); self.result['date']='2026-09-16'
        with self.assertRaises(SafetyError): self.send()

    def test_candidates_must_have_signal(self):
        self.result['candidates']=[{'code':'HK.00001'}]
        with self.assertRaises(SafetyError): self.send()
        self.assertFalse((self.root/'scan.json').exists())

    def test_nonfinite_data_not_published(self):
        self.result['signals']={'HK.00001':{'close':float('nan')}}
        with self.assertRaises(ValueError): self.send()
        self.assertFalse((self.root/'scan.json').exists())
