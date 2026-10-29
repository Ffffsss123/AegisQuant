import copy,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from trading.shared_capital import available_cash,adopt_shared_pool,admission_lock
from trading.capital import config_for_state
from trading.policy import Config,SafetyError
from trading.store import Store
from trading.swing_engine import SwingEngine
from trading.options_observer import Observer

NOW=datetime(2026,9,14,14,tzinfo=timezone.utc)
def states():
    return (dict(shared_capital=True,env='SIMULATE',purpose='strategy_simulation',portfolio_id='p',budget_currency='USD',initial=500,option_budget_usd=500,loss_limit=100,cash=500,orders={},positions={},paused=False,stopped=False),
            dict(shared_capital=True,env='QUOTE_PAPER',portfolio_id='p',initial=500,cap=500,cash=500,fee=5,position=None,trades=[]))
class Shared(unittest.TestCase):
    def test_empty_is_500_not_1000(self):self.assertEqual(available_cash(*states()),500)
    def test_stock_occupancy_and_exit_fee(self):
        s,o=states();s.update(cash=297,positions={'US.A':dict(qty=2,cost=200)})
        self.assertEqual(available_cash(s,o),294)
    def test_option_occupancy_and_exit_fee(self):
        s,o=states();o.update(cash=295,position=dict(entry_price=2))
        self.assertEqual(available_cash(s,o),290)
    def test_both_consume_one_pool(self):
        s,o=states();s.update(cash=297,positions={'US.A':dict(qty=2,cost=200)});o.update(cash=295,position=dict(entry_price=2))
        self.assertEqual(available_cash(s,o),84)
    def test_pending_partial_cancel_and_unknown_fail_closed(self):
        for status in ['SUBMITTED','FILLED_PART','CANCELLING','UNKNOWN']:
            s,o=states();s['orders']={'x':dict(status=status)};self.assertEqual(available_cash(s,o),0)
    def test_cancelled_order_releases_cash(self):
        s,o=states();s['orders']={'x':dict(status='CANCELLED_ALL')};self.assertEqual(available_cash(s,o),500)
    def test_gains_do_not_increase_cap(self):
        s,o=states();s['cash']=550;self.assertEqual(available_cash(s,o),500)
    def test_loss_reduces_cash(self):
        s,o=states();o['cash']=450;self.assertEqual(available_cash(s,o),450)
    def test_negative_cash_cannot_buy(self):
        s,o=states();s['cash']=100;o['cash']=100;self.assertEqual(available_cash(s,o),0)
    def test_wrong_identity_or_environment(self):
        for field,value in [('portfolio_id','foreign'),('env','REAL'),('initial',300),('cap',300),('shared_capital',False)]:
            s,o=states();o[field]=value
            with self.assertRaises(SafetyError):available_cash(s,o)
    def test_full_pool_allocation_only_when_explicit(self):
        self.assertEqual(config_for_state(states()[0]).validate().allocation,1)
        with self.assertRaises(SafetyError):Config(allocation=1).validate()
    def test_engine_available_cash(self):
        s,o=states();o.update(cash=295,position=dict(entry_price=2))
        class Broker:
            def settled_cash(self):return 10000
        e=SwingEngine(Broker(),None,config_for_state(s));e.option_state=lambda:o
        self.assertEqual(e.stock_cash(s),287)
    def test_migration_preserves_controls_and_history_idempotently(self):
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'p');od=Store(Path(root)/'o');s,o=states()
            s.update(shared_capital=False,option_budget_usd=300,paused=True,stopped=True,marks=[{'old':True}]);o.update(shared_capital=False,initial=300,cash=300,cap=300,marks=[{'old':True}])
            db.save(s);od.save(o);a,b=adopt_shared_pool(db,od,NOW)
            self.assertTrue(a['paused']);self.assertTrue(a['stopped']);self.assertEqual(a['marks'],s['marks']);self.assertEqual(b['marks'],o['marks'])
            self.assertEqual(available_cash(a,b),500);self.assertEqual(adopt_shared_pool(db,od,NOW),(a,b));db.close();od.close()
    def test_actual_options_entry_respects_shared_stock_spending(self):
        from tests.test_options_observer import candidate,parent
        # Exercise the existing option selector with a depleted shared pool.
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'o');s,o=states();s.update(cash=4,entry_after_session='2026-09-01',marks=[dict(time=NOW.isoformat(),equity_usd=500)])
            o.update(budget_currency='USD',paused=False,claims=[])
            e=Observer(None,db,news=lambda *args:dict(multiplier=1));e._enter(o,[candidate()],parent(),s,{},NOW)
            self.assertFalse(o['trades']);self.assertEqual(o['shared_available_usd'],4);db.close()
    def test_option_can_use_more_than_previous_300_limit(self):
        from tests.test_options_observer import candidate,parent
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'o');s,o=states();s.update(entry_after_session='2026-09-01',marks=[dict(time=NOW.isoformat(),equity_usd=500)])
            o.update(budget_currency='USD',paused=False,claims=[])
            row=candidate();row['book'].update(bid=4.89,ask=4.90)
            e=Observer(None,db,news=lambda *args:dict(multiplier=1));e._enter(o,[row],parent(),s,{},NOW)
            self.assertEqual(len(o['trades']),1);self.assertAlmostEqual(o['cash'],5);self.assertEqual(available_cash(s,o),0);db.close()
    def test_insufficient_shared_cash_rejects_otherwise_affordable_option(self):
        from tests.test_options_observer import candidate,parent
        with tempfile.TemporaryDirectory() as root:
            db=Store(Path(root)/'o');s,o=states();s.update(cash=250,entry_after_session='2026-09-01',marks=[dict(time=NOW.isoformat(),equity_usd=500)])
            o.update(budget_currency='USD',paused=False,claims=[])
            row=candidate();row['book'].update(bid=2.99,ask=3)
            Observer(None,db,news=lambda *args:dict(multiplier=1))._enter(o,[row],parent(),s,{},NOW)
            self.assertFalse(o['trades']);db.close()
    def test_admission_mutex_excludes_another_process(self):
        import subprocess,sys
        with tempfile.TemporaryDirectory() as root:
            code="import fcntl,sys;f=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
            path=str(Path(root)/'data/trading/shared_admission.lock')
            with admission_lock(root):
                r=subprocess.run([sys.executable,'-c',code,path],capture_output=True)
                self.assertNotEqual(r.returncode,0)
            r=subprocess.run([sys.executable,'-c',code,path],capture_output=True);self.assertEqual(r.returncode,0)
