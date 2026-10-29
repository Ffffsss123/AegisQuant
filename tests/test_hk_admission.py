import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from test_shared_capital import states
from trading.hk_admission import reserve_buy
from trading.hk_reservations import Reservations
from trading.hk_shared_admission import exposure
from trading.policy import SafetyError

class HKAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'data/trading/hk_risk_snapshot.json';self.path.parent.mkdir(parents=True)
        self.now=datetime.now(timezone.utc);stamp=self.now.isoformat()
        self.stock,self.options=states();self.stock.update(account='us',hk_account='8653')
        self.journal=Reservations(self.root/'journal.sqlite','p')
        self.row=dict(as_of=stamp,account='8653',us_account='us',portfolio_id='p',env='SIMULATE',currency='USD',budget_usd=500,loss_limit_usd=100,reconciled=True,occupied_usd='0',realized_net_usd='0',unrealized_net_usd='0',pending_or_unknown=False,entry_block=False)
        self.path.write_text(json.dumps(self.row))
        self.request=dict(side='BUY',env='SIMULATE',account='8653',code='HK.00001',qty=100,price=10,notional_hkd='1000',tag='HK-SIM-test')
        self.recon=dict(reconciled=True,account='us',hk_account='8653',portfolio_id='p',pending_or_unknown=False,option_transport_active=False,as_of=stamp,hk_cash_cap_hkd=1000000)
        self.news=dict(version=3,required_sources=['wire'],health={k:dict(ok=True,checked_at=stamp) for k in ('wire','HK.00001')},events=[])
        self.fx=dict(verified=True,source='fixture',as_of=stamp,hkd_per_usd=8)
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def reserve(self):
        return reserve_buy(self.request,stock=self.stock,options=self.options,controls={},reconciliation=self.recon,
            news=self.news,fx=self.fx,journal=self.journal,root=self.root,now=self.now)
    def test_migration_blocks_before_reserving_hk_cash(self):
        with self.assertRaisesRegex(SafetyError,'Capital migration pending'):
            reserve_buy(self.request,stock=self.stock,options=self.options,
                controls={'capital_migration_pending':True},reconciliation=self.recon,
                news=self.news,fx=self.fx,journal=self.journal,root=self.root,now=self.now)
        self.assertEqual(self.journal.committed_and_held(),0)
        self.assertEqual(json.loads(self.path.read_text()),self.row)

    def test_reserves_once_and_immediately_blocks_other_markets(self):
        self.assertTrue(self.reserve())
        self.assertGreater(float(self.journal.committed_and_held()),125)
        self.assertTrue(exposure(self.stock,self.now,self.root)['entry_block'])
        with self.assertRaises(SafetyError):self.reserve()
    def test_us_and_option_spending_share_one_budget(self):
        self.stock.update(cash=297,positions={'US.A':dict(qty=2,cost=200)},marks=[dict(time=self.now.isoformat(),equity_usd=500)])
        self.options.update(cash=295,position=dict(entry_price=2),valuation_valid=True,marks=[dict(time=self.now.isoformat(),equity_usd=500)])
        with self.assertRaises(SafetyError):self.reserve()
        self.assertEqual(self.journal.committed_and_held(),0)
    def test_loss_pause_news_and_reconciliation_block_before_reservation(self):
        self.stock['cash']=400
        with self.assertRaises(SafetyError):self.reserve()
        self.stock['cash']=500;self.stock['paused']=True
        with self.assertRaises(SafetyError):self.reserve()
        self.stock['paused']=False;self.news['health']['HK.00001']['ok']=False
        with self.assertRaises(SafetyError):self.reserve()
        self.news['health']['HK.00001']['ok']=True;self.recon['pending_or_unknown']=True
        with self.assertRaises(SafetyError):self.reserve()
        self.assertEqual(self.journal.committed_and_held(),0)
    def test_stale_valuation_and_unknown_transport_block(self):
        self.stock.update(positions={'US.A':dict(qty=1,cost=100)},marks=[dict(time=(self.now-timedelta(seconds=31)).isoformat(),equity_usd=500)])
        with self.assertRaises(SafetyError):self.reserve()
        self.stock['positions']={};self.recon['option_transport_active']=True
        with self.assertRaises(SafetyError):self.reserve()
    def test_publication_failure_keeps_hold_and_invalidates_snapshot(self):
        with patch('trading.hk_admission.os.replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):self.reserve()
        self.assertGreater(self.journal.committed_and_held(),0)
        self.assertFalse(self.path.exists())
        with self.assertRaises(SafetyError):exposure(self.stock,self.now,self.root)
    def test_crash_during_reservation_cannot_leave_old_snapshot_usable(self):
        with patch.object(self.journal,'reserve',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):self.reserve()
        self.assertFalse(self.path.exists())
    def test_two_us_holdings_block_hk_before_reservation(self):
        self.stock['positions']={'US.A':dict(qty=1),'US.B':dict(qty=1)}
        with self.assertRaisesRegex(SafetyError,'position limit'):self.reserve()
        self.assertEqual(self.journal.committed_and_held(),0)
    def test_unresolved_hk_holds_use_position_slots(self):
        self.journal.reserve('one',10,500);self.journal.reserve('two',10,500)
        with self.assertRaisesRegex(SafetyError,'position limit'):self.reserve()
        self.assertEqual(float(self.journal.committed_and_held()),20)
