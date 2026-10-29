import json,sqlite3,tempfile,unittest
from pathlib import Path
from trading.desktop_news import hk_claimed_codes
from trading.hk_submission import SubmissionJournal

class HKNewsClaims(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'claims.sqlite'
    def tearDown(self):self.tmp.cleanup()
    def write(self,**extra):
        j=SubmissionJournal(self.path,'8653')
        row=dict(account='8653',env='SIMULATE',code='HK.09626');row.update(extra)
        j.db.execute('INSERT INTO submissions VALUES(?,?,?,NULL,NULL)',('HK-SIM-test',json.dumps(row),'2026-09-17T00:00:00+00:00'));j.close()
    def test_missing_does_not_create(self):
        self.assertEqual(hk_claimed_codes(self.path),set());self.assertFalse(self.path.exists())
    def test_unresolved_claim_is_watched_without_order_id_or_position_report(self):
        self.write();self.assertEqual(hk_claimed_codes(self.path),{'HK.09626'})
    def test_mismatched_account_cannot_refresh_news(self):
        self.write(account='other')
        with self.assertRaises(ValueError):hk_claimed_codes(self.path)
    def test_real_claim_rejected(self):
        self.write(env='REAL')
        with self.assertRaises(ValueError):hk_claimed_codes(self.path)
    def test_corrupt_ledger_not_silently_empty(self):
        self.path.write_text('broken')
        with self.assertRaises(sqlite3.DatabaseError):hk_claimed_codes(self.path)
