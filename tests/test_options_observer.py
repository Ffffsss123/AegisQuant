import tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
from trading.options_observer import Observer,QuoteOnly,contract,shortlist,VERSION
from trading.policy import SafetyError
from trading.store import Store

NOW=datetime(2026,9,14,14,tzinfo=timezone.utc)
def candidate():
    return dict(code='US.TEST261016C10000',underlying='US.TEST',expiry='2026-10-16',right='CALL',standard=True,
        multiplier=100,delta=.5,open_interest=1000,volume=100,snapshot_date='2026-09-14',snapshot_time=NOW.isoformat(),
        book=dict(bid=.049,ask=.05,bid_time=NOW.isoformat(),ask_time=NOW.isoformat()))
def signal():return dict(buy=True,sell=False,liquid=True,fast=11,slow=10,strength=.2)
def parent():return dict(complete=True,date='2026-09-11',generated_at='stamp',signals={'US.TEST':signal()})
def paper():return dict(env='SIMULATE',purpose='strategy_simulation',paused=False,stopped=False,initial=600,
                        cash=600,fx=.6,loss_limit=30,entry_after_session='2026-09-11',marks=[dict(time=NOW.isoformat(),equity_usd=600)])
def static():return dict(code='US.TEST261016C10000',stock_owner='US.TEST',strike_time='2026-10-16',strike_price=10,
                          option_type='CALL',option_standard_type='STANDARD',suspension=False,lot_size=100)
def snap():return dict(code='US.TEST261016C10000',stock_owner='US.TEST',update_time='2026-09-14 10:00:00',
                       option_valid=True,suspension=False,option_contract_multiplier=100,option_delta=.5,
                       option_open_interest=1000,volume=100,ask_price=.05,bid_price=.049)

class Quotes(unittest.TestCase):
    def test_no_trading_methods(self):
        q=QuoteOnly.__new__(QuoteOnly)
        for name in ('place','cancel','bind'):
            with self.assertRaises(SafetyError):getattr(q,name)('anything')
    def test_standard_quote_normalization(self):
        c=contract(static(),snap(),NOW);self.assertEqual(c['multiplier'],100);self.assertNotIn('book',c)
    def test_adjusted_missing_and_stale_excluded(self):
        for change in ({'option_delta':None},{'option_contract_multiplier':10},{'volume':99},{'option_open_interest':999},
                       {'update_time':'2026-09-14 09:50:00'},{'update_time':'2026-09-14 10:00:01'},{'option_valid':False}):
            with self.subTest(change=change),self.assertRaises((SafetyError,ValueError)):contract(static(),dict(snap(),**change),NOW)
        with self.assertRaises(SafetyError):contract(dict(static(),option_standard_type='NONSTANDARD'),snap(),NOW)
    def test_mismatched_identity(self):
        with self.assertRaises(SafetyError):contract(static(),dict(snap(),stock_owner='US.OTHER'),NOW)
    def test_shortlist_includes_put_trend_but_not_illiquid(self):
        s=parent();s['signals']['US.DOWN']=dict(signal(),buy=False,fast=9,strength=-.3)
        s['signals']['US.BAD']=dict(signal(),liquid=False,strength=9)
        rows,total=shortlist(s);self.assertEqual(total,2);self.assertEqual(rows[0]['code'],'US.DOWN')
    def test_scope_is_explicit_and_bounded(self):
        rows,total=shortlist(dict(signals={str(i):dict(signal(),strength=i) for i in range(25)}))
        self.assertEqual((len(rows),total),(20,25))

class Paper(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'s.db');self.b=Mock(spec=['book','execution_session','option_chain','snapshots','reset_books'])
        self.b.book.return_value=candidate()['book'];self.b.execution_session.return_value=('2026-09-14','2026-09-11')
        self.e=Observer(self.b,self.db,news=lambda c,n:dict(multiplier=1));self.s=self.e.load(NOW)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def enter(self,c=None,p=None,controls=None):self.e.enter(self.s,[c or candidate()],parent(),p or paper(),controls or {},NOW)
    def test_flat_off_session_releases_books_once_and_preserves_work(self):
        self.b.execution_session.return_value=None
        self.s['work']={'underlying':'US.TEST','qualified':[candidate()]}
        original=self.s['work'].copy()
        self.b.reset_books.side_effect=SafetyError('unsubscribe unavailable')
        with self.assertRaises(SafetyError):self.e.step(self.s,parent(),paper(),{},NOW)
        self.b.reset_books.side_effect=None
        self.e.step(self.s,parent(),paper(),{},NOW)
        self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=5))
        self.assertEqual(self.b.reset_books.call_count,2)
        self.assertEqual(self.s['work'],original)
        self.b.book.assert_not_called();self.b.option_chain.assert_not_called()
        self.b._off_session_books_released=False
        self.s['position']=candidate()
        self.e.step(self.s,parent(),paper(),{},NOW)
        self.assertEqual(self.b.reset_books.call_count,2)

    def test_completed_batch_rotates_without_expanding_scope_or_skipping_wait(self):
        scan=parent();scan['signals']={f'US.T{i}':dict(signal(),strength=i) for i in range(25)}
        key='2026-09-14:stamp'
        self.s['cycle']=dict(key=key,index=20,started_at=NOW.isoformat(),finished_at=NOW.isoformat(),counts={},scanned=20,contracts=0)
        self.e.step(self.s,scan,paper(),{},NOW+timedelta(seconds=1799))
        self.assertIsNotNone(self.s['cycle'])
        self.e.step(self.s,scan,paper(),{},NOW+timedelta(seconds=1800))
        self.assertIsNone(self.s['cycle']);self.assertEqual(self.s['scan_cursor']['offset'],20)
        self.s['next_step_at']=(NOW+timedelta(hours=2)).isoformat()
        self.e.step(self.s,scan,paper(),{},NOW+timedelta(seconds=1801))
        self.assertEqual(self.s['cycle']['offset'],20)
        self.assertEqual(len(self.s['watch_underlyings']),5)
        self.assertNotIn('US.T24',self.s['watch_underlyings'])
        self.e.step(self.s,dict(scan,generated_at='new'),paper(),{},NOW+timedelta(seconds=1802))
        self.assertEqual(self.s['cycle']['offset'],0)
        self.assertEqual(len(self.s['watch_underlyings']),20)

    def test_budget_includes_both_fee_reserves(self):
        c=candidate();c['book'].update(bid=.059,ask=.06);self.enter(c);self.assertIsNone(self.s['position'])
        self.enter();self.assertEqual(self.s['cash'],5);self.assertEqual(self.s['position']['code'],candidate()['code'])
        self.assertTrue(self.s['trades'][0]['hypothetical'])
    def test_single_contract_no_additions(self):
        self.enter();self.enter();self.assertEqual(len(self.s['trades']),1);self.assertEqual(self.s['trades'][0]['qty'],1)
    def test_paused_or_stopped_blocks_entries(self):
        for controls in ({'paused':True},{'stopped':True}):self.enter(controls=controls);self.assertFalse(self.s['trades'])
    def test_stock_loss_limit_prevents_paper_entry(self):
        p=paper();p['marks'][-1]['equity_usd']=569;self.enter(p=p);self.assertFalse(self.s['trades'])
    def test_wrong_account_or_stale_stock_valuation_blocks(self):
        p=paper();p['env']='REAL';self.enter(p=p);self.assertFalse(self.s['trades'])
        p=paper();p['marks'][-1]['time']=(NOW-timedelta(seconds=31)).isoformat();self.enter(p=p);self.assertFalse(self.s['trades'])
    def test_future_stock_valuation_and_wrong_fx_blocks(self):
        p=paper();p['marks'][-1]['time']=(NOW+timedelta(seconds=1)).isoformat();self.enter(p=p);self.assertFalse(self.s['trades'])
        p=paper();p['fx']=.7;self.enter(p=p);self.assertFalse(self.s['trades'])
    def test_unknown_existing_ledger_is_not_reset(self):
        self.s['env']='REAL';self.db.save(self.s)
        with self.assertRaises(SafetyError):self.e.load(NOW)
    def test_news_block_and_reduction_respected(self):
        for value in (0,.5):
            self.e.news=lambda c,n:dict(multiplier=value);self.enter();self.assertFalse(self.s['trades'])
    def test_quote_stale_or_wide_blocks(self):
        c=candidate();c['book']['bid_time']=(NOW-timedelta(seconds=31)).isoformat();self.enter(c);self.assertFalse(self.s['trades'])
        c=candidate();c['book']['bid']=.04;self.enter(c);self.assertFalse(self.s['trades'])
    def test_pause_allows_trend_exit_and_uses_bid(self):
        self.enter();s=parent();s['signals']['US.TEST'].update(fast=9,sell=True)
        self.e.position(self.s,s,NOW,{'paused':True})
        self.assertIsNone(self.s['position']);self.assertAlmostEqual(self.s['cash'],4.9)
        self.assertEqual(self.s['trades'][-1]['price'],.049)
    def test_due_exit_independent_of_missing_trend(self):
        self.enter();self.s['position']['expiry']='2026-09-21'
        self.e.position(self.s,dict(signals={}),NOW,{})
        self.assertIsNone(self.s['position']);self.assertEqual(self.s['trades'][-1]['reason'],'expiry_7_days')
    def test_missing_exit_quote_keeps_position_not_fake_zero(self):
        self.enter();self.s['position']['expiry']='2026-09-21';self.b.book.side_effect=SafetyError('missing')
        self.e.position(self.s,dict(signals={}),NOW,{})
        self.assertTrue(self.s['paused']);self.assertIsNotNone(self.s['position']);self.assertFalse(self.s['valuation_valid'])
    def test_restart_retains_claims_and_cash(self):
        self.enter();s=parent();s['signals']['US.TEST']['fast']=9;self.e.position(self.s,s,NOW,{})
        e=Observer(self.b,self.db,news=lambda c,n:dict(multiplier=1));self.s=e.load(NOW)
        self.s['cash']=15;self.enter();self.assertEqual(len(self.s['trades']),2)
    def test_stopped_does_not_invent_a_close(self):
        self.enter();self.s['position']['expiry']='2026-09-21';self.e.position(self.s,parent(),NOW,{'stopped':True})
        self.assertIsNotNone(self.s['position']);self.assertTrue(self.s['exit_required'])
    def test_incomplete_scan_never_fetches_entry_chain(self):
        self.e.step(self.s,dict(parent(),complete=False),paper(),{},NOW)
        self.b.option_chain.assert_not_called();self.assertEqual(self.s['status'],'waiting_daily_scan')
    def test_pipeline_records_real_sources_and_budget_exclusion(self):
        self.b.option_chain.return_value=[static()];self.b.snapshots.return_value=[dict(snap(),ask_price=1,bid_price=.99)]
        self.e.step(self.s,parent(),paper(),{},NOW)
        self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=10))
        self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=20))
        self.assertEqual(self.s['cycle']['scanned'],1);self.assertEqual(self.s['cycle']['counts']['budget_including_roundtrip_reserve'],1)
        self.assertFalse(self.s['trades']);self.b.book.assert_not_called()
        self.assertEqual(self.db.db.execute("select count(*) from events where kind like 'real_option_%'").fetchone()[0],2)
    def test_book_rejection_receipts_are_bounded_and_keep_source_time(self):
        import json
        self.b.option_chain.return_value=[static()];self.b.snapshots.return_value=[snap()]
        self.b.book.side_effect=SafetyError('Spread too wide')
        for seconds in (0,10,20,30):self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=seconds))
        self.assertEqual(self.b.book.call_count,2)
        from trading.quote_feed import QuoteUnavailable
        for seconds,status in ((40,'quote_expired'),(50,'connection_interrupted'),(60,'provider_private_detail')):
            self.b.book.side_effect=QuoteUnavailable('provider message must not be persisted',status)
            self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=seconds))
        self.e.step(self.s,parent(),paper(),{},NOW+timedelta(seconds=121))
        self.assertEqual(self.b.book.call_count,5)
        receipts=[json.loads(row[0]) for row in self.db.db.execute("select body from events where kind='option_book_assessment'")]
        self.assertEqual([r['reason'] for r in receipts],['wide_spread','quote_expired','connection_interrupted','quote_unavailable','snapshot_expired'])
        self.assertTrue(all(datetime.fromisoformat(r['snapshot_time'])==NOW for r in receipts))
        self.assertFalse(self.s['trades']);self.assertIsNotNone(self.s['work'])
    def test_report_cannot_claim_broker_execution_or_acceptance(self):
        self.enter();self.e.report(self.s,NOW,Path(self.tmp.name))
        import json
        r=json.loads((Path(self.tmp.name)/'data/trading/options.report.json').read_text())
        self.assertFalse(r['acceptance']);self.assertEqual(r['broker_orders_submitted'],0);self.assertIsNone(r['equity_usd'])

if __name__=='__main__':unittest.main()
