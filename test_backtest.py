import unittest
from backtest import run


class BacktestTests(unittest.TestCase):
    def rows(self, closes):
        return [dict(date='2024-01-%02d' % (i+1), open=float(p), close=float(p)) for i,p in enumerate(closes)]

    def test_next_open_and_accounting(self):
        rows=self.rows([10, 11, 12, 20, 5, 4])
        summary, curve, trades=run(rows, fast=1, slow=2, capital=1000, fee_bps=0, slip_bps=0)
        self.assertEqual(trades[0]['date'], '2024-01-03')
        self.assertEqual(trades[0]['price'], 12)
        self.assertEqual(trades[-1]['date'], '2024-01-06')
        self.assertEqual(summary['final_equity'], 336)
        self.assertAlmostEqual(summary['max_drawdown'], 1-336/1664)

    def test_future_does_not_change_past(self):
        rows=self.rows([10,11,12,13,14,15])
        _, curve, _=run(rows, fast=1, slow=2)
        rows[-1]['close']=999
        _, changed, _=run(rows, fast=1, slow=2)
        self.assertEqual(curve[:-1], changed[:-1])

    def test_costs_and_lots(self):
        summary, curve, trades=run(self.rows([10,11,12,13,14]), fast=1, slow=2, capital=1000, fee_bps=10, slip_bps=20, lot=10)
        self.assertTrue(all(p['cash']>=0 for p in curve))
        self.assertEqual(trades[0]['qty'], 80)
        self.assertAlmostEqual(trades[0]['price'], 12*1.002)
        self.assertAlmostEqual(summary['total_fees'], 80*12*1.002*.001)

    def test_flat_market_no_orders(self):
        summary, _, _=run(self.rows([10]*5), fast=1, slow=2)
        self.assertEqual(summary['orders'],0)
        self.assertEqual(summary['total_return'],0)


if __name__=='__main__':
    unittest.main()
