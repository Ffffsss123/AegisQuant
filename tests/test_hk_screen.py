import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from trading.hk_screen import screen
class HKScreen(unittest.TestCase):
    def run_screen(self,held=()):
        now=datetime(2026,9,18,tzinfo=timezone.utc)
        with patch('trading.hk_screen.validate_reference',return_value=8),patch('trading.hk_screen.reviewed_class',return_value={'kind':'COMMON'}),patch('trading.hk_screen.evaluate',return_value=dict(buy=True,sell=False,liquid=True,strength=.2,orders_enabled=False)):
            return screen('.',self.universe,self.history,self.snap,[],'2026-09-17',{},now,held=held)
    def setUp(self):
        self.universe={'HK.09678':dict(lot_size=20)};self.history={'HK.09678':[dict(close=50)]};self.snap={'HK.09678':dict(code='HK.09678',lot_size=20,suspension=False,update_time='2026-09-17 16:09:00')}
    def test_candidate_is_not_order_permission(self):
        r=self.run_screen();self.assertTrue(r['complete']);self.assertEqual(len(r['candidates']),1);self.assertFalse(r['orders_enabled'])
    def test_missing_snapshot_blocks_complete_publication(self):
        self.snap={};r=self.run_screen();self.assertFalse(r['complete']);self.assertFalse(r['candidates'])
    def test_ambiguous_suspension_never_approves_candidate(self):
        for value in (0,1,'False',None):
            with self.subTest(value=value):
                self.snap['HK.09678']['suspension']=value
                r=self.run_screen();self.assertFalse(r['complete']);self.assertFalse(r['candidates'])
    def test_suspended_security_is_excluded(self):
        self.snap['HK.09678']['suspension']=True
        r=self.run_screen();self.assertTrue(r['complete']);self.assertFalse(r['candidates'])
    def test_held_exit_signal_survives_directory_removal(self):
        self.universe={};r=self.run_screen(held=['HK.09678']);self.assertIn('HK.09678',r['signals']);self.assertFalse(r['candidates'])
