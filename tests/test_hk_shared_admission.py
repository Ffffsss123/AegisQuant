import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from trading.hk_shared_admission import exposure
from trading.shared_capital import available_cash
from trading.option_transport import ready_for_reservation
from trading.policy import SafetyError
from test_shared_capital import states

class HKSharedAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'data/trading/hk_risk_snapshot.json';self.path.parent.mkdir(parents=True)
        self.now=datetime.now(timezone.utc)
        self.stock,self.options=states();self.stock.update(account='us',hk_account='hk')
        self.row=dict(as_of=self.now.isoformat(),account='hk',us_account='us',portfolio_id='p',env='SIMULATE',currency='USD',budget_usd=500,loss_limit_usd=100,reconciled=True,occupied_usd=120,realized_net_usd=-10,unrealized_net_usd=-5,pending_or_unknown=False,entry_block=False)
        self.save()
    def tearDown(self):self.tmp.cleanup()
    def save(self):self.path.write_text(json.dumps(self.row))
    def test_bound_missing_stale_or_wrong_identity_cannot_be_zero(self):
        for key,value in [('as_of',(self.now-timedelta(seconds=31)).isoformat()),('account','other'),('reconciled',False),('env','REAL'),('pending_or_unknown',None)]:
            old=self.row[key];self.row[key]=value;self.save()
            with self.assertRaises(SafetyError):exposure(self.stock,self.now,self.root)
            self.row[key]=old
        self.path.unlink()
        with self.assertRaises(SafetyError):exposure(self.stock,self.now,self.root)
    def test_occupation_and_realized_loss_deduct_once(self):
        with patch('trading.hk_shared_admission.ROOT',self.root):
            self.assertEqual(available_cash(self.stock,self.options),370)
        self.assertEqual(exposure(self.stock,self.now,self.root)['total_net_pnl_usd'],-15)
    def test_unknown_hk_order_closes_shared_admission(self):
        self.row['pending_or_unknown']=True;self.save()
        with patch('trading.hk_shared_admission.ROOT',self.root):
            self.assertEqual(available_cash(self.stock,self.options),0)
    def test_broker_option_loss_gate_includes_hk(self):
        self.row.update(occupied_usd=0,realized_net_usd=-100,unrealized_net_usd=0);self.save()
        with patch('trading.hk_shared_admission.ROOT',self.root),self.assertRaises(SafetyError):
            ready_for_reservation(self.stock,self.options,{})
    def test_unbound_existing_us_pool_unchanged(self):
        self.stock.pop('hk_account');self.assertEqual(available_cash(self.stock,self.options),500)
    def test_stock_risk_equity_counts_hk_pnl_once(self):
        from trading.swing_engine import SwingEngine
        from trading.capital import config_for_state
        engine=SwingEngine(None,None,config_for_state(self.stock));engine.option_state=lambda:self.options
        with patch('trading.hk_shared_admission.ROOT',self.root):
            self.assertEqual(engine.combined_equity(self.stock,self.now),485)
    def test_quote_paper_option_loss_gate_includes_hk(self):
        from trading.options_observer import Observer
        from trading.store import Store
        self.row.update(occupied_usd=0,realized_net_usd=-100,unrealized_net_usd=0);self.save()
        self.stock.update(entry_after_session='2026-01-01',marks=[dict(time=self.now.isoformat(),equity_usd=500)])
        self.options.update(paused=False,budget_currency='USD',claims=[])
        db=Store(self.root/'options.sqlite')
        try:
            with patch('trading.hk_shared_admission.ROOT',self.root),patch('trading.options_observer.option_candidates') as selector:
                Observer(None,db)._enter(self.options,[],{},self.stock,{},self.now)
                selector.assert_not_called()
        finally:db.close()
    def test_hk_slots_include_unresolved_holds_and_validate_portfolio(self):
        from trading.hk_reservations import Reservations
        from trading.hk_shared_admission import occupied_slots
        j=Reservations(self.path.parent/'hk_reservations.sqlite','p')
        try:
            j.reserve('pending',10,500)
            self.assertEqual(occupied_slots(self.stock,self.now,self.root),1)
            j.db.execute("UPDATE identity SET portfolio='other'")
            with self.assertRaises(SafetyError):occupied_slots(self.stock,self.now,self.root)
        finally:j.close()
    def test_missing_bound_hk_slots_fail_closed(self):
        from trading.hk_shared_admission import occupied_slots
        with self.assertRaises(SafetyError):occupied_slots(self.stock,self.now,self.root)
        self.assertFalse((self.path.parent/'hk_reservations.sqlite').exists())
        self.stock.pop('hk_account')
        self.assertEqual(occupied_slots(self.stock,self.now,self.root),0)
