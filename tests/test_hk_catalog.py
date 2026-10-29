import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from trading.hk_catalog import build
from trading.policy import SafetyError
class HKCatalog(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,17,12,tzinfo=timezone.utc)
        self.rows=[{'A':'Updated as at 17/09/2026'},dict(A='09626',B='BILIBILI-W',C='Equity',D='Equity Securities (Main Board)',E='20',F='identity',Q='HKD',P='1')]
        self.basic=dict(queried_at=self.now.isoformat(),rows=[dict(code='HK.09626',name='哔哩哔哩',lot_size=20,delisting=False)])
    def run_build(self):
        with patch('trading.hk_catalog.workbook_rows',return_value=self.rows):return build(b'fixture',self.basic,self.now)
    def test_equity_identity_does_not_approve_common_share(self):
        r=self.run_build()['rows']['HK.09626'];self.assertFalse(r['share_class_reviewed']);self.assertFalse(r['orders_enabled'])
    def test_old_directory_rejected(self):
        self.rows[0]['A']='Updated as at 16/09/2026'
        with self.assertRaises(SafetyError):self.run_build()
    def test_lot_conflict_not_admitted(self):
        self.basic['rows'][0]['lot_size']=100
        with self.assertRaises(SafetyError):self.run_build()
    def test_non_equity_excluded(self):
        self.rows[1]['C']='Leveraged and Inverse Products'
        with self.assertRaises(SafetyError):self.run_build()
