import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from trading.hk_reservations import Reservations
from trading.hk_risk_snapshot import publish_initial_flat
from trading.hk_shared_admission import exposure
from trading.policy import SafetyError

class InitialRiskSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'data/trading/hk_risk_snapshot.json'
        self.journal=Reservations(self.root/'journal.sqlite','p')
        self.now=datetime.now(timezone.utc)
        self.stock=dict(env='SIMULATE',account='us',portfolio_id='p',initial=500,loss_limit=100)
        self.accounts=[dict(acc_id='hk',trd_env='SIMULATE',acc_status='ACTIVE',
            trdmarket_auth=['HK'],acc_type='CASH',sim_acc_type='STOCK')]
        self.snapshot=dict(checked_at=(self.now-timedelta(seconds=7)).isoformat(),
            funds=dict(return_code=0,response=[dict(currency='N/A',cash=1000000,
                total_assets=1000000,frozen_cash=0,market_val=0,interest_charged_amount=0)]),
            positions=dict(return_code=0,response=[]),orders=dict(return_code=0,response=[]))
    def tearDown(self):self.journal.close();self.tmp.cleanup()
    def publish(self):
        return publish_initial_flat(self.path,stock=self.stock,hk_account='hk',
            accounts=self.accounts,snapshot=self.snapshot,journal=self.journal,now=self.now)
    def test_source_time_preserved_and_shared_buys_blocked(self):
        row=self.publish()
        self.assertEqual(row['as_of'],self.snapshot['checked_at'])
        self.assertNotIn('hk_account',self.stock)
        result=exposure(dict(self.stock,hk_account='hk'),self.now,self.root)
        self.assertTrue(result['entry_block']);self.assertEqual(result['occupied_usd'],0)
        self.assertEqual(json.loads(self.path.read_text()),row)
    def test_history_cannot_be_reset_even_after_release(self):
        self.journal.reserve('old',20,500)
        self.journal.release_unfilled('old',broker_status='CANCELLED_ALL',filled_qty=0,
            reconciled=True,evidence='broker')
        with self.assertRaises(SafetyError):self.publish()
        self.assertFalse(self.path.exists())
    def test_stale_or_nonflat_broker_snapshot_preserves_previous(self):
        original=self.publish();self.snapshot['positions']['response']=[dict(code='HK.00700',qty=100)]
        with self.assertRaises(SafetyError):self.publish()
        self.assertEqual(json.loads(self.path.read_text()),original)
        self.snapshot['positions']['response']=[]
        self.now+=timedelta(seconds=31)
        with self.assertRaises(SafetyError):self.publish()
    def test_account_and_journal_identity_rejected(self):
        self.stock['portfolio_id']='wrong'
        with self.assertRaises(SafetyError):self.publish()
        self.stock['portfolio_id']='p';self.accounts[0]['trd_env']='REAL'
        with self.assertRaises(SafetyError):self.publish()
