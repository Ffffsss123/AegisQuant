import json,tempfile,unittest
from pathlib import Path
from trading.hk_reservations import Reservations
from trading.policy import SafetyError

class Identity(unittest.TestCase):
    def test_identity_survives_restart_and_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.sqlite';r=Reservations(p,'portfolio')
            i=dict(tag='entry',env='SIMULATE',account='8653',side='BUY',code='HK.09678')
            r.reserve('entry',100,500,identity=i);r.close();r=Reservations(p,'portfolio')
            self.assertEqual(json.loads(r.db.execute('SELECT body FROM reservation_identity').fetchone()[0]),i)
            for altered in (None,dict(i,code='HK.02718')):
                with self.assertRaises(SafetyError):r.reserve('entry',100,500,identity=altered)
            r.reserve('entry',100,500,identity=i);r.close()
    def test_failed_capacity_leaves_no_identity_or_hold(self):
        with tempfile.TemporaryDirectory() as d:
            r=Reservations(Path(d)/'r.sqlite','portfolio')
            i=dict(tag='entry',env='SIMULATE',account='8653',side='BUY',code='HK.09678')
            with self.assertRaises(SafetyError):r.reserve('entry',100,50,identity=i)
            self.assertEqual(r.db.execute('SELECT count(*) FROM reservation_identity').fetchone()[0],0)
            self.assertEqual(r.db.execute('SELECT count(*) FROM holds').fetchone()[0],0)
            r.close()

    def test_active_unknown_hold_keeps_slot_and_legacy_blocks(self):
        from trading.simulation_slots import occupied_slots
        with tempfile.TemporaryDirectory() as d:
            r=Reservations(Path(d)/'r.sqlite','portfolio')
            i=dict(tag='entry',env='SIMULATE',account='8653',side='BUY',code='HK.09678')
            r.reserve('entry',100,500,identity=i)
            records=r.slot_records('8653','hk')
            self.assertEqual(records[0]['state'],'ENTRY_UNKNOWN')
            self.assertEqual(occupied_slots(records,env='SIMULATE')['HK_STOCK'],1)
            with self.assertRaises(SafetyError):r.slot_records('other','hk')
            r.reserve('legacy',100,500)
            with self.assertRaises(SafetyError):r.slot_records('8653','hk')
            r.close()
