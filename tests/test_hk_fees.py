import unittest
from decimal import Decimal
from trading.hk_fees import order_estimate
from trading.policy import SafetyError

class HKFees(unittest.TestCase):
    def test_small_order_and_roundtrip(self):
        r=order_estimate([1000]);self.assertEqual(Decimal(r['total_hkd']),Decimal('19.15'))
        self.assertEqual(Decimal(r['total_hkd'])*2,Decimal('38.30'))
        self.assertFalse(r['broker_confirmed']);self.assertFalse(r['orders_enabled'])
    def test_split_fills_do_not_repeat_broker_minimum(self):
        r=order_estimate([500,500]);self.assertEqual(r['components']['commission'],'3.00')
        self.assertEqual(r['components']['platform'],'15')
        self.assertGreater(Decimal(r['total_hkd']),Decimal(order_estimate([1000])['total_hkd']))
    def test_stamp_only_exempt_when_confirmed(self):
        self.assertEqual(order_estimate([1000],stamp_exempt=True)['components']['stamp'],'0')
        self.assertEqual(order_estimate([1000])['components']['stamp'],'1')
        with self.assertRaises(SafetyError):order_estimate([1000],stamp_exempt='ETF')
    def test_large_notional_settlement_has_no_old_cap(self):
        self.assertEqual(order_estimate([10000000])['components']['settlement'],'420.00')
    def test_invalid_or_zero_amount(self):
        for amounts in ([],[0],[-1],['NaN'],[True]):
            with self.assertRaises(SafetyError):order_estimate(amounts)
