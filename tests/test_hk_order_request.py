import unittest
from datetime import datetime, timezone, timedelta
from trading.hk_order_request import prepare
from trading.policy import SafetyError

class HKOrderRequest(unittest.TestCase):
    def setUp(self):
        now=datetime(2026,9,17,2,tzinfo=timezone.utc)
        self.args=dict(account='fixture8653', accounts=[dict(acc_id='fixture8653',
            trd_env='SIMULATE',acc_status='ACTIVE',trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')],
            account_checked_at=now.isoformat(), calendar=[dict(time='2026-09-17',trade_date_type='WHOLE')],
            now=now,code='HK.00001',side='BUY',qty=100,
            book=dict(bid=10,ask=10.02,bid_time=now.isoformat(),ask_time=now.isoformat()),
            metadata=dict(code='HK.00001',verified=True,suspended=False,kind='COMMON',date='2026-09-17',lot_size=100,tick_size=.02),tag='HK-SIM-fixture')
    def test_request_remains_simulation_and_not_authorized(self):
        request=prepare(**self.args)
        self.assertEqual(request['price'],10.02);self.assertEqual(request['currency'],'HKD')
        self.assertFalse(request['orders_enabled'])
    def test_lunch_closed_and_account_confusion_rejected(self):
        for hour in (4,8):
            args=dict(self.args,now=self.args['now'].replace(hour=hour),account_checked_at=self.args['now'].replace(hour=hour).isoformat())
            with self.assertRaises(SafetyError):prepare(**args)
        self.args['accounts'][0]['trd_env']='REAL'
        with self.assertRaises(SafetyError):prepare(**self.args)
    def test_quote_age_lot_grid_and_metadata_rejected(self):
        for changes in [dict(qty=1),dict(book=dict(self.args['book'],ask=10.01)),
                        dict(book=dict(self.args['book'],bid_time=(self.args['now']-timedelta(seconds=31)).isoformat())),
                        dict(metadata=dict(self.args['metadata'],date='2026-09-16'))]:
            with self.assertRaises(SafetyError):prepare(**dict(self.args,**changes))
    def test_sell_does_not_short_or_exceed_strategy_position(self):
        with self.assertRaises(SafetyError):prepare(**dict(self.args,side='SELL',held_qty=0,position_key='HK-SIM-entry'))
        self.assertEqual(prepare(**dict(self.args,side='SELL',held_qty=100,position_key='HK-SIM-entry'))['price'],10)
