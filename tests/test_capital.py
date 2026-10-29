import tempfile,unittest,csv
from datetime import timedelta
from pathlib import Path
from trading.capital import migrate_usd,config_for_state
from trading.policy import Config,SafetyError
from trading.store import Store
from trading.report import weekly_rows,weekly_report
from trading.swing_engine import SwingEngine
from tests import test_trading as h
from tests import test_swing as sw
from tests import test_options_observer as opt

class Dollars(unittest.TestCase):
    def test_independent_runner_maintenance_uses_joint_archive_and_controls(self):
        from unittest.mock import patch
        from trading.paper_strategy import independent_order_maintenance
        state=dict(env='SIMULATE',capital_mode='independent_sleeves_v1',account='chosen',acceptance_reset_id='reset')
        reset=dict(status='initialized_paused',archive_complete=True,request_id='reset',archive='/archive')
        with patch('trading.paper_strategy.read_json',return_value=reset),patch('trading.simulation_history.load_reset_history',return_value={'sealed':True}) as history,patch('trading.simulation_reconcile.cancel_expired_locked',return_value=[]) as maintenance:
            self.assertEqual(independent_order_maintenance(Path('/stage'),object(),state),[])
            self.assertTrue(maintenance.call_args.kwargs['honor_controls'])
            self.assertTrue(maintenance.call_args.kwargs['honor_news'])
            self.assertEqual(set(maintenance.call_args.args[2]),{'US_STOCK','US_OPTION'})
            with self.assertRaises(SafetyError):independent_order_maintenance(Path('/stage'),object(),dict(state,env='REAL'))
            with self.assertRaises(SafetyError):independent_order_maintenance(Path('/stage'),object(),dict(state,acceptance_reset_id='other'))
            self.assertEqual(maintenance.call_count,1)
            with patch('trading.simulation_admission.observe_locked_us_loss',return_value={'entry_blocked':True}) as observe:
                self.assertEqual(independent_order_maintenance(Path('/stage'),object(),state,observe_loss=True),{'entry_blocked':True})
                default_provider=observe.call_args.kwargs['hk_loss_provider']
                self.assertTrue(callable(default_provider))
                provider=lambda: {'entry_blocked':True}
                independent_order_maintenance(Path('/stage'),object(),state,observe_loss=True,hk_loss_provider=provider)
                self.assertIs(observe.call_args.kwargs['hk_loss_provider'],provider)
                self.assertTrue(observe.call_args.kwargs['service_orders'])
                self.assertIsNone(observe.call_args.kwargs['hk_account'])
                self.assertEqual(observe.call_args.kwargs['risk_path'],Path('/stage/data/trading/simulation_loss_latch.sqlite'))

    def test_bound_hk_loss_uses_separate_context_and_closes_on_failure(self):
        import json,sys
        from types import SimpleNamespace
        from unittest.mock import patch,Mock
        from trading.paper_strategy import bound_hk_loss
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'data/trading';base.mkdir(parents=True)
            stock=dict(env='SIMULATE',capital_mode='independent_sleeves_v1',account='8652',hk_account='8653',portfolio_id='p',hk_history_start='2026-06-25')
            context=Mock();factory=Mock(return_value=context)
            sdk=SimpleNamespace(OpenSecTradeContext=factory,TrdMarket=SimpleNamespace(HK='HK'),SecurityFirm=SimpleNamespace(FUTUAU='AU'))
            with patch('trading.paper_strategy.ledger',return_value=stock),patch.dict(sys.modules,moomoo=sdk):
                with self.assertRaises(FileNotFoundError):bound_hk_loss(root,Mock(),history_start='2026-06-25')
                factory.assert_not_called()
                (base/'hk_fx_binding.json').write_text('{}')
                with patch('trading.hk_fx.validate_accounting_binding'),patch('trading.hk_risk_producer.collect_bound_loss',side_effect=SafetyError('read failed')) as collect:
                    with self.assertRaisesRegex(SafetyError,'read failed'):
                        bound_hk_loss(root,Mock(),history_start='2026-06-25')
                    self.assertEqual(collect.call_args.args[1].account,'8653')
                    self.assertIs(collect.call_args.args[1].context,context)
                context.close.assert_called_once_with()
                self.assertEqual(factory.call_args.kwargs['filter_trdmarket'],'HK')
                stock['hk_empty_history']=dict(directory='/evidence',raw_sha256='pinned')
                with patch('trading.simulation_history.load_empty_hk_history',return_value=dict(raw_sha256='pinned',next_query_start='2026-09-22')) as history,patch('trading.hk_fx.validate_accounting_binding'),patch('trading.hk_risk_producer.collect_bound_loss',return_value={}) as collect:
                    bound_hk_loss(root,Mock(),history_start='2026-06-25')
                    self.assertEqual(collect.call_args.kwargs['history_start'],'2026-09-22')
                    self.assertEqual(history.call_args.kwargs['history_start'],'2026-06-25')
                stock['hk_empty_history']['raw_sha256']='wrong'
                before=factory.call_count
                with patch('trading.simulation_history.load_empty_hk_history',return_value=dict(raw_sha256='pinned',next_query_start='2026-09-22')):
                    with self.assertRaisesRegex(SafetyError,'digest mismatch'):
                        bound_hk_loss(root,Mock(),history_start='2026-06-25')
                self.assertEqual(factory.call_count,before)

    def test_staged_snapshot_preserves_state_events_and_never_overwrites(self):
        import sqlite3
        from contextlib import closing
        from trading.capital import snapshot_staged_ledger
        with tempfile.TemporaryDirectory() as directory:
            db=Store(Path(directory)/'source.sqlite');target=Path(directory)/'copy.sqlite'
            try:
                state=dict(cash=500,paused=True,stopped=True,alerts=['retained'])
                db.save(state,'user_stop')
                with db.lock():snapshot_staged_ledger(db,target)
                with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as copy:
                    self.assertEqual(copy.execute('SELECT body FROM state').fetchall(),db.db.execute('SELECT body FROM state').fetchall())
                    self.assertEqual(copy.execute('SELECT * FROM events').fetchall(),db.db.execute('SELECT * FROM events').fetchall())
                with self.assertRaises(FileExistsError):snapshot_staged_ledger(db,target)
                with self.assertRaises(FileExistsError):snapshot_staged_ledger(db,db.path)
                self.assertEqual(db.load(),state)
            finally:db.close()

    def test_migration_compare_write_preserves_concurrent_state(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            first=Store(Path(directory)/'state.sqlite');second=Store(first.path)
            try:
                original=dict(cash=500,paused=True,marks=[],alerts=[])
                first.save_if_current(None,original,'staged_init')
                proposed=dict(original,capital_mode='independent_sleeves_v1')
                changed=copy.deepcopy(original);changed['marks'].append(dict(equity_usd=500));changed['stopped']=True
                second.save(changed,'concurrent_control')
                with self.assertRaises(SafetyError):first.save_if_current(original,proposed,'migration')
                self.assertEqual(first.load(),changed)
                self.assertEqual(first.db.execute("SELECT COUNT(*) FROM events WHERE kind='migration'").fetchone()[0],0)
                with self.assertRaises(SafetyError):first.save_if_current(None,original,'duplicate_init')
                updated=dict(changed,capital_mode='independent_sleeves_v1')
                first.save_if_current(changed,updated,'migration')
                self.assertEqual(second.load(),updated)
                self.assertEqual(first.db.execute("SELECT COUNT(*) FROM events WHERE kind='migration'").fetchone()[0],1)
            finally:second.close();first.close()

    def test_migration_receipt_recovers_committed_stage_without_recredit(self):
        from trading.capital import apply_staged_migration
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'stage.sqlite';db=Store(path)
            target=dict(env='SIMULATE',cash=500,paused=True)
            try:self.assertTrue(apply_staged_migration(db,'migration-1',None,target))
            finally:db.close()
            db=Store(path)
            try:
                self.assertFalse(apply_staged_migration(db,'migration-1',None,target))
                with self.assertRaises(SafetyError):apply_staged_migration(db,'migration-1',None,dict(target,cash=1000))
                db.save(dict(target,stopped=True),'user_stop')
                with self.assertRaises(SafetyError):apply_staged_migration(db,'migration-1',None,target)
                self.assertTrue(db.load()['stopped'])
                self.assertEqual(db.load()['cash'],500)
                self.assertEqual(db.db.execute("SELECT COUNT(*) FROM events WHERE kind='capital_migration_stage'").fetchone()[0],1)
            finally:db.close()

    def test_staged_plan_checks_later_source_before_first_write(self):
        from trading.capital import apply_staged_plan
        with tempfile.TemporaryDirectory() as directory:
            option=Store(Path(directory)/'option.sqlite');stock=Store(Path(directory)/'stock.sqlite')
            old=dict(cash=500,paused=True);new=dict(old,sleeve='US_STOCK')
            try:
                stock.save(dict(old,stopped=True))
                stages=[(option,None,dict(old,sleeve='US_OPTION')),(stock,old,new)]
                with self.assertRaises(SafetyError):apply_staged_plan(stages,'plan-1')
                self.assertFalse(option.exists())
                self.assertTrue(stock.load()['stopped'])
                # A freshly reviewed plan preserves the stop instead of clearing it.
                stages[1]=(stock,stock.load(),dict(stock.load(),sleeve='US_STOCK'))
                self.assertEqual(apply_staged_plan(stages,'plan-1'),[True,True])
                self.assertEqual(apply_staged_plan(stages,'plan-1'),[False,False])
            finally:stock.close();option.close()

    def test_staged_plan_lock_conflict_writes_nothing_and_releases_locks(self):
        from trading.capital import apply_staged_plan_locked
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);first=Store(root/'a.sqlite');last=Store(root/'z.sqlite')
            target=dict(cash=500,paused=True)
            stages=[(first,None,target),(last,None,target)]
            try:
                with last.lock():
                    with self.assertRaises(SafetyError):apply_staged_plan_locked(root,stages,'locked-plan')
                self.assertFalse(first.exists());self.assertFalse(last.exists())
                # The failed attempt must release the earlier lock and admission.
                self.assertEqual(apply_staged_plan_locked(root,stages,'locked-plan'),[True,True])
                self.assertEqual(apply_staged_plan_locked(root,stages,'locked-plan'),[False,False])
            finally:last.close();first.close()

    def test_durable_plan_reopens_and_rejects_changed_recovery_inputs(self):
        from trading.capital import seal_staged_plan
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ledger=Store(root/'target.sqlite');journal=Store(root/'journal.sqlite')
            stages=[(ledger,None,dict(env='SIMULATE',cash=500,paused=True))]
            try:
                self.assertTrue(seal_staged_plan(journal,stages,'sealed-1'))
                journal.close();journal=Store(root/'journal.sqlite')
                self.assertFalse(seal_staged_plan(journal,stages,'sealed-1'))
                with self.assertRaises(SafetyError):
                    seal_staged_plan(journal,[(ledger,None,dict(cash=1000))],'sealed-1')
                self.assertFalse(ledger.exists())
                self.assertFalse(journal.load()['execution_enabled'])
                self.assertEqual(journal.load()['stages'][0]['target']['cash'],500)
            finally:journal.close();ledger.close()

    def test_sealed_plan_is_required_and_bound_to_actual_writes(self):
        from trading.capital import seal_staged_plan,apply_sealed_plan_locked
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ledger=Store(root/'target.sqlite');journal=Store(root/'journal.sqlite')
            stages=[(ledger,None,dict(env='SIMULATE',cash=500,paused=True))]
            try:
                with self.assertRaises(SafetyError):apply_sealed_plan_locked(root,journal,stages,'plan')
                self.assertFalse(ledger.exists())
                seal_staged_plan(journal,stages,'plan')
                wrong=[(ledger,None,dict(env='SIMULATE',cash=1000,paused=True))]
                with self.assertRaises(SafetyError):apply_sealed_plan_locked(root,journal,wrong,'plan')
                self.assertFalse(ledger.exists())
                self.assertEqual(apply_sealed_plan_locked(root,journal,stages,'plan'),[True])
                self.assertEqual(apply_sealed_plan_locked(root,journal,stages,'plan'),[False])
                self.assertEqual(ledger.load()['cash'],500)
                self.assertFalse(journal.load()['execution_enabled'])
            finally:journal.close();ledger.close()

    def test_usd_values_do_not_depend_on_fx(self):
        c=Config(budget_usd=500,loss_limit_usd=60,option_cap_usd=300).validate()
        for fx in (.6,.9,1):self.assertEqual((c.budget(fx),c.loss(fx),c.option_cap(fx)),(500,60,300))
    def test_legacy_values_still_match_old_probe(self):
        c=Config();self.assertEqual((c.budget(.6),c.loss(.6),c.option_cap(.6)),(600,30,15))
    def test_invalid_usd_budget(self):
        for kw in (dict(budget_usd=500),dict(budget_usd=891,loss_limit_usd=60,option_cap_usd=300),dict(budget_usd=500,loss_limit_usd=60,option_cap_usd=501)):
            with self.assertRaises(SafetyError):Config(**kw).validate()
    def test_funding_withdrawal_is_not_a_loss(self):
        def mark(t,e):return dict(time=t,equity_usd=e,stock_pnl_usd=0,option_pnl_usd=0,fees_usd=0)
        now=h.NOW;s=dict(initial=500,marks=[mark(now.isoformat(),600),mark((now+timedelta(days=7)).isoformat(),500)],
                         capital_changes=[dict(time=(now+timedelta(days=1)).isoformat(),old_initial=600,new_initial=500,amount_usd=-100)])
        rows,_=weekly_rows(s);self.assertEqual([r['pnl_usd'] for r in rows],[0,0]);self.assertEqual(rows[-1]['capital_flow_usd'],-100)
    def test_flow_timestamp_compares_instants(self):
        s=dict(initial=500,marks=[dict(time='2026-09-08T10:00:00-04:00',equity_usd=500,stock_pnl_usd=0,option_pnl_usd=0,fees_usd=0)],
               capital_changes=[dict(time='2026-09-08T13:59:00+00:00',old_initial=600,amount_usd=-100)])
        self.assertEqual(weekly_rows(s)[0][0]['pnl_usd'],0)

class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);self.db=Store(root/'p.db');self.od=Store(root/'o.db');self.b=sw.Broker()
        self.e=h.Engine(self.b,self.db,Config());self.s=self.e.bind('chosen','SIMULATE',.6,h.NOW,extra=dict(purpose='strategy_simulation',strategy_version='swing_1_5_v1',entry_after_session='2026-09-04'))
        self.e.mark(self.s,h.NOW)
        self.o=opt.Observer(None,self.od).load(h.NOW);self.probe=dict(account='chosen',orders={})
    def tearDown(self):self.db.close();self.od.close();self.tmp.cleanup()
    def migrate(self):return migrate_usd(self.b,self.db,self.od,self.probe,h.NOW+timedelta(seconds=1),500,60,300)
    def test_preserves_marks_controls_and_does_not_duplicate_cash_flow(self):
        self.s['paused']=True;self.s['stopped']=True;self.db.save(self.s)
        s,o=self.migrate();self.assertEqual((s['initial'],s['cash'],s['loss_limit'],s['reserve_usd']),(500,500,60,390))
        self.assertTrue(s['paused']);self.assertTrue(s['stopped']);self.assertEqual(s['marks'],self.s['marks'])
        self.assertEqual((o['initial'],o['cash'],o['cap']),(300,300,300));self.assertEqual(s['portfolio_id'],o['portfolio_id'])
        self.migrate();self.assertEqual(len(self.db.load()['capital_changes']),1)
    def test_existing_trade_prevents_budget_reset(self):
        self.s['orders']={'previous':{}};self.db.save(self.s)
        with self.assertRaises(SafetyError):self.migrate()
    def test_unknown_broker_order_prevents_migration(self):
        self.b.remote=[dict(id='foreign',status='FILLED_ALL')]
        with self.assertRaises(SafetyError):self.migrate()
    def test_stock_reserves_options_allocation(self):
        s,o=self.migrate();e=SwingEngine(self.b,self.db,config_for_state(s));e.option_state=self.od.load
        self.assertEqual(e.load()['initial'],500);self.assertEqual(e.stock_cash(s),200)
        self.assertEqual(e.combined_equity(dict(s,marks=[dict(time=h.NOW.isoformat(),equity_usd=500)]),h.NOW),500)
    def test_combined_loss_pauses_stock_entries(self):
        s,o=self.migrate();o['cash']=240;self.od.save(o)
        e=SwingEngine(self.b,self.db,config_for_state(s));e.option_state=self.od.load
        scan=dict(date='2026-09-04',version='swing_1_5_v1',complete=True,signals={},candidates=[])
        result=e.tick(scan,h.NOW+timedelta(seconds=2));self.assertTrue(result['paused']);self.assertEqual(result['combined_risk_equity_usd'],440)
    def test_mismatched_options_identity_blocks(self):
        s,o=self.migrate();o['portfolio_id']='other';self.od.save(o)
        e=SwingEngine(self.b,self.db,config_for_state(s));e.option_state=self.od.load
        with self.assertRaises(SafetyError):e.combined_equity(s,h.NOW)
    def test_stale_option_valuation_blocks_new_stock_risk(self):
        s,o=self.migrate();o.update(position={'code':'test'},valuation_valid=True,marks=[dict(time=(h.NOW-timedelta(seconds=31)).isoformat(),equity_usd=300)]);self.od.save(o)
        e=SwingEngine(self.b,self.db,config_for_state(s));e.option_state=self.od.load
        with self.assertRaises(SafetyError):e.combined_equity(s,h.NOW)
    def test_usd_weekly_export_has_native_values(self):
        s,o=self.migrate();e=h.Engine(self.b,self.db,config_for_state(s));e.mark(s,h.NOW+timedelta(seconds=2))
        dest=Path(self.tmp.name)/'report';rows=weekly_report(s,dest)
        self.assertEqual(rows[-1]['equity_usd'],500);self.assertEqual(rows[-1]['pnl_usd'],0)
        self.assertNotIn('NZD',(dest/'weekly.md').read_text());self.assertNotIn('nzd',(dest/'weekly.csv').read_text())

class InitialHKMigration(unittest.TestCase):
    def test_empty_identity_and_history_are_preserved(self):
        from trading.capital import verify_initial_hk_journals
        from trading.hk_submission import SubmissionJournal
        from trading.hk_reservations import Reservations
        with tempfile.TemporaryDirectory() as tmp:
            journal=SubmissionJournal(Path(tmp)/'sub.sqlite','8653')
            holds=Reservations(Path(tmp)/'holds.sqlite','portfolio')
            try:
                self.assertTrue(verify_initial_hk_journals(journal.db,holds.db,account='8653',portfolio='portfolio'))
                with self.assertRaises(SafetyError):
                    verify_initial_hk_journals(journal.db,holds.db,account='8653',portfolio='other')
                holds.db.execute("INSERT INTO holds VALUES('old','0','RELEASED','evidence')")
                with self.assertRaises(SafetyError):
                    verify_initial_hk_journals(journal.db,holds.db,account='8653',portfolio='portfolio')
                self.assertEqual(holds.db.execute('SELECT count(*) FROM holds').fetchone()[0],1)
            finally:journal.db.close();holds.close()
