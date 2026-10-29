import unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone
from trading.hk_submission import SubmissionJournal
from trading.hk_reservations import Reservations
from trading.hk_settlement import settle_buys
from trading.hk_fees import order_estimate
from trading.policy import SafetyError

class HKSettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.now=datetime.now(timezone.utc)
        self.sub=SubmissionJournal(root/'sub.sqlite','8653');self.res=Reservations(root/'res.sqlite','p')
        self.request=dict(account='8653',env='SIMULATE',market='HK',currency='HKD',side='BUY',
            order_type='NORMAL',time_in_force='DAY',tag='HK-SIM-buy',code='HK.00001',qty=100,price=10)
        self.sub.claim(self.request,self.now);self.res.reserve('HK-SIM-buy',130,500)
        self.fill=dict(order_id='1',qty='20',average_hkd='10',status='FILLED_PART')
        self.inventory=dict(account='8653',inventory_reconciled=True,as_of=self.now.isoformat(),fills={'HK-SIM-buy':self.fill})
        self.fees={}
    def tearDown(self):self.sub.close();self.res.close();self.tmp.cleanup()
    def settle(self):return settle_buys(self.sub,self.res,self.inventory,self.fees,fixed_hkd_per_usd=8,now=self.now)
    def test_partial_order_keeps_full_hold_then_terminal_partial_uses_actual_cost(self):
        self.assertEqual(self.settle(),[]);self.assertEqual(float(self.res.committed_and_held()),130)
        self.fill['status']='CANCELLED_PART'
        fee=order_estimate([100,100])['total_hkd']
        self.fees={'1':dict(source='simulation_estimate',amount_hkd=fee,execution_amounts_hkd=[100,100],evidence='actual deal query')}
        self.settle();expected=(200+float(fee))/8
        self.assertAlmostEqual(float(self.res.committed_and_held()),expected)
        self.settle();self.assertAlmostEqual(float(self.res.committed_and_held()),expected)
    def test_missing_or_fabricated_fee_does_not_reduce_hold(self):
        self.fill['status']='CANCELLED_PART'
        with self.assertRaises(SafetyError):self.settle()
        self.fees={'1':dict(source='simulation_estimate',amount_hkd=0,execution_amounts_hkd=[200],evidence='deal')}
        with self.assertRaises(SafetyError):self.settle()
        self.assertEqual(float(self.res.committed_and_held()),130)
    def test_unfilled_cancel_releases_only_after_terminal_evidence(self):
        self.fill.update(qty='0',average_hkd='0',status='CANCELLED_ALL')
        self.settle();self.assertEqual(self.res.committed_and_held(),0)
    def test_fee_overrun_retained_not_capped_to_old_reservation(self):
        self.fill.update(qty='100',status='FILLED_ALL')
        self.fees={'1':dict(source='broker',amount_hkd=50,evidence='fee response')}
        self.settle();self.assertEqual(float(self.res.committed_and_held()),131.25)

    def test_closed_buy_history_replay_does_not_reopen_or_charge_twice(self):
        self.fill.update(qty='100',status='FILLED_ALL')
        self.fees={'1':dict(source='broker',amount_hkd=20,evidence='fee response')}
        self.settle()
        self.res.settle_closed_position('HK-SIM-buy',net_proceeds_usd=140,sold_qty=100,
            order_ids=['sell'],position_flat=True,no_pending_orders=True,reconciled=True,evidence='sell query')
        self.assertEqual(self.settle()[0]['transition'],'already_closed')
        self.assertEqual(self.res.committed_and_held(),0)
        self.fees['1']['amount_hkd']=30
        with self.assertRaises(SafetyError):self.settle()

class HKCloseTests(unittest.TestCase):
    setUp=HKSettlementTests.setUp
    tearDown=HKSettlementTests.tearDown
    settle=HKSettlementTests.settle
    def prepare_close(self,qty=100):
        self.fill.update(qty='100',status='FILLED_ALL')
        self.fees={'1':dict(source='broker',amount_hkd=20,evidence='buy fee')};self.settle()
        self.sub.claim(dict(self.request,side='SELL',tag='HK-SIM-sell',position_key='HK-SIM-buy',qty=qty,price=11),self.now)
        self.inventory['fills']['HK-SIM-sell']=dict(order_id='2',qty=str(qty),average_hkd='11',status='FILLED_ALL')
        self.inventory['pending_or_unknown']=False
        self.fees['2']=dict(source='broker',amount_hkd=20,evidence='sell fee')
    def close_settle(self,rate=8):
        from trading.hk_settlement import settle_closes
        return settle_closes(self.sub,self.res,self.inventory,self.fees,fixed_hkd_per_usd=rate,now=self.now)
    def test_full_close_net_fees_and_idempotent_replay(self):
        self.prepare_close();self.close_settle()
        self.assertEqual(self.res.committed_and_held(),0)
        self.assertEqual(float(self.res.realized_pnl()),7.5)
        self.close_settle();self.assertEqual(float(self.res.realized_pnl()),7.5)
    def test_partial_close_keeps_full_capital(self):
        self.prepare_close(40);self.assertEqual(self.close_settle(),[])
        self.assertEqual(float(self.res.committed_and_held()),127.5)
    def test_pending_close_and_fee_failure_do_not_release(self):
        self.prepare_close();self.inventory['pending_or_unknown']=True
        self.assertEqual(self.close_settle(),[])
        self.inventory['pending_or_unknown']=False;self.fees.pop('2')
        with self.assertRaises(SafetyError):self.close_settle()
        self.assertEqual(float(self.res.committed_and_held()),127.5)
    def test_changed_fx_rejected(self):
        self.prepare_close()
        with self.assertRaises(SafetyError):self.close_settle(7.8)
