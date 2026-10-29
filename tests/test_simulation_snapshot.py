import fcntl
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from trading.simulation_snapshot import locked_ledgers
from trading.policy import SafetyError


class Snapshot(unittest.TestCase):
    def test_locks_remain_held_until_validation_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ledger.sqlite'
            c=sqlite3.connect(p);c.execute('CREATE TABLE state(id INTEGER, body TEXT)')
            c.execute('INSERT INTO state VALUES(1,?)',(json.dumps({'env':'SIMULATE'}),));c.commit();c.close()
            with locked_ledgers({'stock':p}) as states:
                self.assertEqual(states['stock']['env'],'SIMULATE')
                with open(str(p)+'.lock','a') as other:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(SafetyError):
                    with locked_ledgers({'stock':p}):pass
            with locked_ledgers({'stock':p}):pass
            with self.assertRaises(SafetyError):
                with locked_ledgers({'stock':p,'option':p}):pass

    def test_missing_ledger_is_never_initialized(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing.sqlite'
            with self.assertRaises(SafetyError):
                with locked_ledgers({'option':p}):pass
            self.assertFalse(p.exists())
