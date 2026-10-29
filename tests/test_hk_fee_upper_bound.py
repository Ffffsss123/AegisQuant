import unittest,random
from decimal import Decimal
from trading.hk_fees import order_estimate,order_upper_bound
from trading.hk_settlement import fee_amount
from trading.policy import SafetyError

class HKFeeUpperBoundTests(unittest.TestCase):
    def test_random_execution_splits_never_exceed_bound(self):
        rng=random.Random(57)
        for _ in range(150):
            qty=rng.randint(1,100)
            executions=[Decimal(rng.randint(1,500000))/100 for _ in range(qty)]
            actual=Decimal(order_estimate(executions)['total_hkd'])
            bound=Decimal(order_upper_bound(sum(executions),qty)['total_hkd'])
            self.assertGreaterEqual(bound,actual)
    def test_single_execution_one_share_has_no_extra_rounding(self):
        self.assertEqual(order_upper_bound(100,1)['total_hkd'],order_estimate([100])['total_hkd'])
    def test_order_based_estimate_explicitly_records_quantity_and_model(self):
        fee=order_upper_bound(1000,100)
        record=dict(source='simulation_order_upper_bound',amount_hkd=fee['total_hkd'],filled_qty=100,evidence='audited cumulative order response')
        self.assertEqual(fee_amount(record,Decimal(1000),100),Decimal(fee['total_hkd']))
        with self.assertRaises(SafetyError):fee_amount(record,Decimal(1000),101)
        with self.assertRaises(SafetyError):order_upper_bound(1000,1.5)

    def test_cumulative_order_fallback_never_marks_estimate_broker_confirmed(self):
        from trading.hk_settlement import simulated_order_fee_records
        row=dict(account='8653',inventory_reconciled=True,as_of='2026-09-17T10:00:00+00:00',
            fills={'buy':dict(order_id='1',qty='10',average_hkd='50',status='FILLED_ALL'),
                   'open':dict(order_id='2',qty='1',average_hkd='50',status='FILLED_PART')})
        fees=simulated_order_fee_records(row)
        self.assertEqual(set(fees),{'1'})
        self.assertFalse(fees['1']['broker_confirmed'])
        self.assertEqual(fees['1']['source'],'simulation_order_upper_bound')
