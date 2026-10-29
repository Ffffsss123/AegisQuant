import unittest
from trading.market_capabilities import market_capabilities

class CapabilityTests(unittest.TestCase):
    def test_futures_category_never_claims_execution_or_live_permission(self):
        value=market_capabilities()
        self.assertFalse(value['live_execution_enabled'])
        futures=next(x for x in value['markets'] if x['category']=='FUTURE')
        self.assertFalse(futures['simulation_execution'])
        self.assertIn('unverified',futures['scope'])
        self.assertNotIn('Binance',[x['market'] for x in value['markets']])

    def test_reserved_future_slot_cannot_pass_the_real_entry_gate(self):
        from unittest.mock import patch
        from trading.simulation_admission import check_entry
        from trading.policy import SafetyError
        with patch('trading.simulation_admission.persistent_budget_view',return_value={}):
            with self.assertRaisesRegex(SafetyError,'futures remain research'):
                check_entry(risk_path=None,portfolio='fixture',sleeves={},slots=[],
                    controls={'paused':False,'stopped':False,'reset_pending':False},
                    sleeve='FUTURE',category='FUTURE',required_usd=1,env='SIMULATE')
