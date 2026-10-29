import unittest
from datetime import datetime, timezone
from trading.hk_fx import parse_ecb, validate_reference
from trading.policy import SafetyError

class HKFX(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,3,tzinfo=timezone.utc)
        self.raw=b'<root xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube time="2026-09-16"><Cube currency="USD" rate="1.1537"/><Cube currency="HKD" rate="9.0507"/></Cube></root>'
    def test_dated_reference_has_no_fabricated_time(self):
        r=parse_ecb(self.raw,self.now)
        self.assertNotIn('as_of',r);self.assertEqual(r['time_precision'],'date')
        self.assertFalse(r['orders_enabled']);self.assertGreater(validate_reference(r,self.now),7)
    def test_old_and_future_dates_rejected(self):
        for day in (b'2026-09-15',b'2026-09-18'):
            with self.assertRaises(SafetyError):parse_ecb(self.raw.replace(b'2026-09-16',day),self.now)
    def test_duplicate_missing_invalid_and_unsafe_data(self):
        for raw in (self.raw.replace(b'currency="HKD"',b'currency="USD"'),self.raw.replace(b'9.0507',b'NaN'),self.raw.replace(b'1.1537',b'0'),b'<!DOCTYPE root>'+self.raw):
            with self.assertRaises(SafetyError):parse_ecb(raw,self.now)
    def test_source_or_ratio_changes_rejected(self):
        r=parse_ecb(self.raw,self.now)
        for changes in (dict(source='other'),dict(hkd_per_usd='7.8'),dict(verified=False)):
            with self.assertRaises(SafetyError):validate_reference(dict(r,**changes),self.now)
