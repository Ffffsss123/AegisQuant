import unittest
from trading.hk_tick import tick_size
from trading.policy import SafetyError
from decimal import Decimal
class HKTick(unittest.TestCase):
    def tick(self,p,**kw):return tick_size(p,**dict(dict(table='01',kind='COMMON',session='2026-09-18'),**kw))
    def test_boundaries_use_lower_band_at_exact_limit(self):
        for p,v in [('.25','.001'),('.255','.005'),('10','.005'),('10.01','.01'),('20','.01'),('20.02','.02'),('50','.02'),('50.05','.05'),('100','.05'),('100.1','.1'),('9995','5')]:
            with self.subTest(price=p):self.assertEqual(self.tick(p),Decimal(v))
    def test_other_instruments_or_old_regime_rejected(self):
        for kw in [dict(table='02'),dict(kind='ETF_NORMAL'),dict(session='2026-08-02')]:
            with self.assertRaises(SafetyError):self.tick('60',**kw)
    def test_invalid_prices(self):
        for p in (0,10000,True,'NaN'):
            with self.assertRaises(SafetyError):self.tick(p)
