from unittest.mock import patch as reset_test_patch
import tempfile,unittest,logging
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock,patch
with patch('logging.handlers.TimedRotatingFileHandler',return_value=logging.NullHandler()):
    import moomoo  # SDK import otherwise opens a log outside the test workspace.
from pathlib import Path
from trading.quote_feed import BookCache,QuoteUnavailable,decode_book,quote_context,exchange_time
from trading.policy import SafetyError,quote_price
from trading.paper_probe import ProbeStep
from trading.desktop_worker import resume_empty_probe
from trading.store import Store
from test_trading import Fake,Engine,Config,NOW,book

def message(bid=10,ask=10.01,numeric=True,text=False):
    from moomoo.common.pb.Qot_UpdateOrderBook_pb2 import Response
    from moomoo.common.utils import split_stock_str
    p=Response();p.retType=0
    _,(market,code)=split_stock_str('US.AA');p.s2c.security.market=market;p.s2c.security.code=code
    for name,price in [('orderBookBidList',bid),('orderBookAskList',ask)]:
        r=getattr(p.s2c,name).add();r.price=price;r.volume=10;r.orederCount=1
    if numeric:
        p.s2c.svrRecvTimeBidTimestamp=NOW.timestamp();p.s2c.svrRecvTimeAskTimestamp=NOW.timestamp()
    if text:p.s2c.svrRecvTimeBid=p.s2c.svrRecvTimeAsk='2026-09-08 09:35:00'
    return p

class Quotes(unittest.TestCase):
    def hk_message(self,**kw):
        from moomoo.common.utils import split_stock_str
        p=message(**kw);_,(market,code)=split_stock_str('HK.00700')
        p.s2c.security.market=market;p.s2c.security.code=code
        return p
    def test_hk_text_uses_hong_kong_timezone(self):
        code,b=decode_book(self.hk_message(numeric=False,text=True))
        self.assertEqual(code,'HK.00700')
        expected=datetime(2026,9,8,1,35,tzinfo=timezone.utc)
        self.assertEqual(b['bid_time'],expected.isoformat())
        self.assertEqual(quote_price(b,'BUY',expected),10.01)
    def test_hk_epoch_precedes_text_and_missing_side_blocks(self):
        p=self.hk_message(text=True)
        self.assertEqual(decode_book(p)[1]['bid_time'],NOW.isoformat())
        p.s2c.ClearField('svrRecvTimeAskTimestamp');p.s2c.ClearField('svrRecvTimeAsk')
        with self.assertRaises(QuoteUnavailable):decode_book(p)
    def test_unknown_market_naive_time_not_guessed(self):
        with self.assertRaises(QuoteUnavailable):exchange_time(message(numeric=False,text=True).s2c,'Bid','UNKNOWN')
    def test_numeric_only(self):
        code,b=decode_book(message());self.assertEqual(code,'US.AA');self.assertEqual(quote_price(b,'BUY',NOW),10.01)
    def test_text_only(self):self.assertEqual(decode_book(message(numeric=False,text=True))[1]['bid_time'],NOW.isoformat())
    def test_numeric_preferred(self):
        p=message(text=True);p.s2c.svrRecvTimeBidTimestamp=NOW.timestamp()+.25
        self.assertEqual(datetime.fromisoformat(decode_book(p)[1]['bid_time']).timestamp(),NOW.timestamp()+.25)
    def test_invalid_numeric_falls_back_to_valid_text(self):
        p=message(text=True);p.s2c.svrRecvTimeBidTimestamp=float('nan')
        self.assertEqual(decode_book(p)[1]['bid_time'],NOW.isoformat())
    def test_one_side_absent(self):
        p=message();p.s2c.ClearField('svrRecvTimeAskTimestamp')
        with self.assertRaises(QuoteUnavailable):decode_book(p)
    def test_initial_missing_then_update(self):
        c=BookCache();c.reset(1)
        with self.assertRaises(QuoteUnavailable):c.get(1,'US.AA',NOW)
        c.publish(1,*decode_book(message()));self.assertEqual(c.get(1,'US.AA',NOW)['ask'],10.01)
    def test_invalid_new_message_does_not_pair_with_old_time(self):
        c=BookCache();c.reset(1);c.publish(1,*decode_book(message()))
        c.publish(1,'US.AA',QuoteUnavailable('missing new time'))
        with self.assertRaises(QuoteUnavailable):c.get(1,'US.AA',NOW)
    def test_both_sides_must_be_fresh(self):
        for offset in (-31,1):
            c=BookCache();c.reset(1);_,b=decode_book(message());b['ask_time']=(NOW+timedelta(seconds=offset)).isoformat();c.publish(1,'US.AA',b)
            with self.assertRaises(QuoteUnavailable) as caught:c.get(1,'US.AA',NOW)
            self.assertEqual(caught.exception.status,'quote_expired')
    def test_disconnect_and_old_connection_push(self):
        c=BookCache();c.reset(1);c.publish(1,*decode_book(message()));c.reset()
        with self.assertRaises(QuoteUnavailable):c.get(1,'US.AA',NOW)
        c.reset(2);c.publish(1,*decode_book(message()))
        with self.assertRaises(QuoteUnavailable):c.get(2,'US.AA',NOW)
        c.publish(2,*decode_book(message()));self.assertEqual(c.get(2,'US.AA',NOW)['bid'],10)
    def test_spread_rule_preserved(self):
        b=decode_book(message(ask=11))[1]
        with self.assertRaises(SafetyError):quote_price(b,'BUY',NOW)
    def test_context_uses_network_connection_and_decodes_raw_push(self):
        from types import SimpleNamespace
        from moomoo.common.constant import ProtoId
        from moomoo.common.network_manager import PacketErr
        class Base:
            def __init__(self,**kw):self._conn_id=7;self.on_api_socket_reconnected()
            def on_api_socket_reconnected(self):return 0,''
            def on_disconnect(self,*a):pass
            def handle_push(self,*a):pass
        ctx=quote_context(SimpleNamespace(OpenQuoteContext=Base),'127.0.0.1',11111)
        ctx.handle_push(7,ProtoId.Qot_UpdateOrderBook,1,message(),PacketErr.Ok,'')
        self.assertEqual(ctx.book_cache.get(7,'US.AA',NOW)['ask'],10.01)
        ctx.on_disconnect(7,None,'');ctx.handle_push(7,ProtoId.Qot_UpdateOrderBook,1,message(),PacketErr.Ok,'')
        with self.assertRaises(QuoteUnavailable):ctx.book_cache.get(7,'US.AA',NOW)

class ProbeFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'probe.db');self.b=Fake()
        self.b.now=lambda:NOW;self.b.execution_session=lambda *a,**kw:('2026-09-08','2026-09-04')
        self.b.book=lambda code:book();self.e=Engine(self.b,self.db,Config())
        self.s=self.e.bind('chosen','SIMULATE',.6,NOW)
        self.s.update(purpose='transport_probe',probe_code='US.AA',probe_phase='cancel_test');self.db.save(self.s)
        self.step=ProbeStep(self.e,'US.AA')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_missing_quote_then_good_quote_submits_once(self):
        self.b.book=Mock(side_effect=QuoteUnavailable('cache'))
        self.assertEqual(self.step.tick(self.s,NOW),'waiting_quote');self.assertEqual(self.b.calls,0)
        self.b.book=lambda code:book();self.step.tick(self.s,NOW)
        self.assertEqual(self.s['probe_phase'],'cancel_wait');self.assertEqual(self.b.calls,1)
        self.step.tick(self.s,NOW);self.assertEqual(self.b.calls,1)
    def test_second_preorder_quote_missing_rolls_back_phase_only(self):
        self.b.book=Mock(side_effect=[book(),QuoteUnavailable('cache')])
        self.assertEqual(self.step.tick(self.s,NOW),'waiting_quote')
        self.assertEqual(self.s['probe_phase'],'cancel_test');self.assertEqual(self.s['orders'],{});self.assertEqual(self.b.calls,0)
    def test_quote_expiring_during_submission_defers_without_intent(self):
        old=book();old['bid_time']=(NOW-timedelta(seconds=31)).isoformat()
        self.b.book=Mock(side_effect=[book(),old])
        self.assertEqual(self.step.tick(self.s,NOW),'quote_expired')
        self.assertEqual(self.s['probe_phase'],'cancel_test');self.assertEqual(self.b.calls,0)
    def test_quote_wait_services_cancel_before_timeout(self):
        self.step.tick(self.s,NOW);self.step.wait_started=0
        with patch('trading.paper_probe.time.monotonic',return_value=121):
            with self.assertRaises(QuoteUnavailable) as caught:self.step.tick(self.s,NOW+timedelta(seconds=61))
        self.assertEqual(caught.exception.status,'quote_wait_timeout')
        self.assertEqual(self.b.remote[0]['status'],'CANCELLED_ALL');self.assertTrue(next(iter(self.s['orders'].values()))['cancel_requested'])
    def test_roundtrip_after_cancellation(self):
        self.step.tick(self.s,NOW);self.step.tick(self.s,NOW+timedelta(seconds=61))
        self.assertEqual(self.s['probe_phase'],'buy');self.step.tick(self.s,NOW)
        self.b.fill(1);self.step.tick(self.s,NOW);self.step.tick(self.s,NOW)
        self.b.fill(1);self.assertEqual(self.step.tick(self.s,NOW),'transport_passed')
        self.assertEqual(self.b.pos['US.AA'],0);self.assertEqual(len(self.s['orders']),3)
    def test_accidental_cancel_test_fill_never_passes(self):
        self.step.tick(self.s,NOW);self.b.fill(1)
        with self.assertRaises(SafetyError):self.step.tick(self.s,NOW)
        self.assertNotEqual(self.s['probe_phase'],'complete')
    @reset_test_patch('trading.desktop_worker.ROOT', Path('/nonexistent/test-only-controls'))
    def test_resume_empty_quote_fault(self):
        self.s.update(paused=True,alerts=['SafetyError: Order book timestamp unavailable']);self.db.save(self.s)
        self.assertEqual(resume_empty_probe(self.db,self.b),'chosen');self.assertFalse(self.db.load()['paused'])
    def test_resume_rejects_manual_pause_risk_stop_or_orders(self):
        for alert in ('User paused entries','Cumulative loss limit reached','Position mismatch'):
            self.s.update(paused=True,alerts=[alert]);self.db.save(self.s)
            with self.assertRaises(SafetyError):resume_empty_probe(self.db,self.b)
        self.s.update(paused=True,alerts=['Order book timestamp unavailable']);self.db.save(self.s)
        self.b.remote=[dict(id='x',status='CANCELLED_ALL')]
        with self.assertRaises(SafetyError):resume_empty_probe(self.db,self.b)
