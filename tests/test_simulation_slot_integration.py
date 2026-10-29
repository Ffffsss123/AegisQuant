"""Exercise Engine and HK durable claims together without broker mutations."""
import tempfile
import unittest
from pathlib import Path
from trading.hk_reservations import Reservations
from trading.simulation_slots import ledger_slots, occupied_slots, require_capacity
from trading.policy import SafetyError


class CombinedSlots(unittest.TestCase):
    def test_independent_categories_with_pending_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'hk.sqlite';journal=Reservations(path,'portfolio')
            for n,code in enumerate(('HK.09678','HK.02718')):
                journal.reserve(str(n),100,500,identity=dict(tag=str(n),env='SIMULATE',account='8653',side='BUY',code=code))
            journal.close();journal=Reservations(path,'portfolio')
            states={'stock':dict(env='SIMULATE',account='8652',positions={c:dict(qty=1,kind='STOCK') for c in ('US.A','US.B')},orders={}),
                    'option':dict(env='SIMULATE',account='8652',positions={'US.OPT1':dict(qty=1,kind='OPTION')},orders={'pending':dict(code='US.OPT2',kind='OPTION',side='BUY',status='UNKNOWN')})}
            claims=ledger_slots(states)+journal.slot_records('8653','hk')
            self.assertEqual(occupied_slots(claims,env='SIMULATE'),dict(US_STOCK=2,HK_STOCK=2,OPTION=2,FUTURE=0))
            for category in ('US_STOCK','HK_STOCK','OPTION'):
                with self.assertRaises(SafetyError):require_capacity(claims,category,env='SIMULATE')
            # An option cancellation frees only its own category.
            states['option']['orders']['pending']['status']='CANCELLED_ALL'
            claims=ledger_slots(states)+journal.slot_records('8653','hk')
            self.assertEqual(require_capacity(claims,'OPTION',env='SIMULATE')['OPTION'],1)
            with self.assertRaises(SafetyError):require_capacity(claims,'HK_STOCK',env='SIMULATE')
            with self.assertRaises(SafetyError):occupied_slots(claims,env='REAL')
            journal.close()
