import unittest
from trading.position_display import simulation_details,external_details
class PositionDisplay(unittest.TestCase):
    def test_remaining_cost_and_option_multiplier(self):
        r=simulation_details({},'US.A',dict(qty=2,cost=121.7,fees=3))
        self.assertAlmostEqual(r['buy_price'],60.85)
        self.assertEqual(simulation_details({},'US.X',dict(qty=1,cost=200,multiplier=100))['buy_price'],2)
    def test_order_status_precedes_pause(self):
        s=dict(paused=True,orders={'a':dict(code='US.A',side='SELL',status='FILLED_PART',booked_qty=1)})
        self.assertIn('部分成交',simulation_details(s,'US.A',{})['position_status'])
        s['orders']['a']['status']='UNKNOWN'
        self.assertIn('待确认',simulation_details(s,'US.A',{})['position_status'])
        s['orders']={};s['exit_quote_status']={'US.A':'Spread too wide'}
        self.assertIn('价差过大',simulation_details(s,'US.A',{})['position_status'])
    def test_external_unverified_or_missing_cost_never_zero(self):
        for p in [{},dict(cost_price=10),dict(cost_price=10,cost_price_valid=False),dict(cost_price='nan',cost_price_valid=True)]:self.assertIsNone(external_details(p,'stamp')['buy_price'])
        self.assertEqual(external_details(dict(cost_price=10,cost_price_valid=True),'stamp')['buy_price'],10)
