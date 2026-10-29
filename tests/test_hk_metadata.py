import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from trading.hk_metadata import execution_metadata
from trading.policy import SafetyError
class HKMetadata(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,18,2,tzinfo=timezone.utc)
        self.catalog=dict(source_date='2026-09-18',rows={'HK.00001':dict(lot_size=100,spread_table='1')})
        self.snapshot=dict(code='HK.00001',lot_size=100,suspension=False,update_time='2026-09-18 10:00:00')
    def run_metadata(self,checked=None):
        with patch('trading.hk_metadata.reviewed_class',return_value=dict(kind='COMMON')):
            return execution_metadata('.',self.catalog,self.snapshot,checked or self.now.isoformat(),'HK.00001',60,self.now)
    def test_normalizes_verified_directory_table(self):
        r=self.run_metadata();self.assertEqual(r['tick_size'],'0.05');self.assertFalse(r['orders_enabled'])
    def test_stale_status_or_previous_session_blocked(self):
        with self.assertRaises(SafetyError):self.run_metadata((self.now-timedelta(seconds=31)).isoformat())
        self.snapshot['update_time']='2026-09-17 16:00:00'
        with self.assertRaises(SafetyError):self.run_metadata()
    def test_lot_change_or_suspension_blocked(self):
        self.snapshot['lot_size']=200
        with self.assertRaises(SafetyError):self.run_metadata()
        self.snapshot['lot_size']=100;self.snapshot['suspension']=True
        with self.assertRaises(SafetyError):self.run_metadata()
    def test_other_spread_table_not_guessed(self):
        self.catalog['rows']['HK.00001']['spread_table']='2'
        with self.assertRaises(SafetyError):self.run_metadata()
