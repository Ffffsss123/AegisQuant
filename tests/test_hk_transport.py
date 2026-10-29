import unittest,tempfile,json
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace as NS
import test_hk_order_request as request_fixture
from trading.hk_transport import HKTransport
from trading.hk_submission import SubmissionJournal
from trading.policy import SafetyError

class HKTransportTests(unittest.TestCase):
    def setUp(self):
        fixture=request_fixture.HKOrderRequest();fixture.setUp();self.inputs=fixture.args
        self.inputs['account']='8653';self.inputs['accounts'][0]['acc_id']='8653'
        self.now=self.inputs.pop('now');self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.controls=self.root/'data/trading/simulation_controls.json'
        self.controls.parent.mkdir(parents=True);self.controls.write_text(json.dumps(dict(paused=False,stopped=False,reset_pending=False)))
        self.journal=SubmissionJournal(self.root/'submit.sqlite','8653');self.calls=[];self.audit=[]
        self.row=dict(remark=self.inputs['tag'],order_id='123',code=self.inputs['code'],
            trd_side='BUY',qty=100,price=10.02,order_status='SUBMITTED')
        def place(**kw):self.calls.append(('place',kw));return 0,[self.row]
        def cancel(**kw):self.calls.append(('cancel',kw));return 0,[]
        self.ctx=NS(place_order=place,modify_order=cancel,get_acc_list=lambda:(0,self.inputs['accounts']),order_list_query=lambda **kw:(0,[self.row]))
        self.sdk=NS(RET_OK=0,TrdEnv=NS(SIMULATE='SIMULATE'),TrdSide=NS(BUY='BUY',SELL='SELL'),
            OrderType=NS(NORMAL='NORMAL'),TimeInForce=NS(DAY='DAY'),ModifyOrderOp=NS(CANCEL='CANCEL'))
        self.transport=HKTransport(self.sdk,self.ctx,self.journal,self.root,
            lambda k,v:self.audit.append((k,v)),lambda:self.now)
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def test_migration_activated_during_admission_blocks_before_claim(self):
        def gate(request):
            self.controls.write_text(json.dumps({'capital_migration_pending':True}))
            return True
        with self.assertRaisesRegex(SafetyError,'Capital migration pending'):
            self.transport.submit(self.inputs,gate)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM submissions').fetchone()[0],0)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM aborted_admissions').fetchone()[0],1)

    def test_gate_denial_never_calls_broker(self):
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,lambda r:False)
        self.assertEqual(self.calls,[])
    def test_duplicate_never_reenters_reservation_gate(self):
        gates=[]
        def gate(r):gates.append(r['tag']);return True
        self.transport.submit(self.inputs,gate)
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,gate)
        self.assertEqual(len(gates),1)
        self.assertEqual(len(self.calls),1)
    def test_gate_cannot_change_admitted_quantity(self):
        def gate(r):r['qty']=200;return True
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,gate)
        self.assertEqual(self.calls,[])
    def test_external_mutation_cannot_change_submitted_quantity(self):
        def gate(r):self.inputs['qty']=200;return True
        self.transport.submit(self.inputs,gate)
        self.assertEqual(self.calls[0][1]['qty'],100)
    def test_gate_latency_rechecks_source_quote(self):
        def gate(r):self.now+=timedelta(seconds=31);return True
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,gate)
        self.assertEqual(self.calls,[])
    def test_timeout_claim_survives_no_resend(self):
        def timeout(**kw):self.calls.append(('place',kw));raise TimeoutError()
        self.ctx.place_order=timeout
        with self.assertRaises(TimeoutError):self.transport.submit(self.inputs,lambda r:True)
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,lambda r:True)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.audit[-1][0],'hk_place_uncertain')
    def test_cancel_only_own_order_after_deadline_and_not_confirmation(self):
        self.assertEqual(self.transport.submit(self.inputs,lambda r:True),'123')
        with self.assertRaises(SafetyError):self.transport.cancel(self.inputs['tag'])
        self.now+=timedelta(seconds=60)
        self.assertEqual(self.transport.cancel(self.inputs['tag']),'cancel_requested')
        with self.assertRaises(SafetyError):self.transport.cancel(self.inputs['tag'])
        with self.assertRaises(SafetyError):self.transport.cancel('manual-order')
        self.assertEqual([v[0] for v in self.calls],['place','cancel'])
        for _,kw in self.calls:
            self.assertEqual(kw['trd_env'],'SIMULATE');self.assertEqual(kw['acc_id'],8653)
    def test_cancel_race_with_fill_does_not_modify_order(self):
        self.transport.submit(self.inputs,lambda r:True);self.now+=timedelta(seconds=60)
        self.row['order_status']='FILLED_ALL'
        self.assertEqual(self.transport.cancel(self.inputs['tag']),'already_terminal')
        self.assertEqual(len(self.calls),1)
