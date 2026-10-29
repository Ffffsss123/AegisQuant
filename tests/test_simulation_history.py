import copy,unittest
from trading.simulation_history import reconcile_with_history
from trading.policy import SafetyError

class History(unittest.TestCase):
    def setUp(self):
        self.states={'stock':dict(env='SIMULATE',account='8652',baseline={},positions={},orders={})}
        self.row=dict(id='old',code='US.A',side='SELL',status='FILLED_ALL',qty=1,filled=1,average=12)
        self.archive=dict(env='SIMULATE',account='8652',sealed=True,orders=[self.row])
    def test_old_fill_is_verified_without_rebooking(self):
        before=copy.deepcopy(self.states)
        result=reconcile_with_history(self.states,'8652',[self.row],self.archive)
        self.assertEqual(result['order_owners'],{})
        self.assertEqual(self.states,before)
    def test_changed_missing_duplicate_and_unknown_history_fail(self):
        for rows in ([],[self.row,self.row],[dict(self.row,average=13)],[dict(self.row,status='SUBMITTED')],[self.row,dict(self.row,id='unknown')]):
            with self.subTest(rows=rows),self.assertRaises(SafetyError):
                reconcile_with_history(self.states,'8652',rows,self.archive)
    def test_archive_identity_and_reuse_fail(self):
        for patch in (dict(env='REAL'),dict(account='8653'),dict(sealed=False),dict(orders=[dict(self.row,average=float('nan'))])):
            with self.subTest(patch=patch),self.assertRaises(SafetyError):
                reconcile_with_history(self.states,'8652',[self.row],dict(self.archive,**patch))
        self.states['stock']['orders']['x']=dict(self.row)
        with self.assertRaises(SafetyError):reconcile_with_history(self.states,'8652',[self.row],self.archive)

class ArchiveLoader(unittest.TestCase):
    def test_verified_snapshot_and_tamper_rejection(self):
        import tempfile,json,sqlite3,hashlib
        from pathlib import Path
        from trading.simulation_history import load_reset_history
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);records=[]
            for name in ('paper.sqlite','probe.sqlite'):
                path=root/name;db=sqlite3.connect(path)
                db.execute('CREATE TABLE state(id INTEGER, body TEXT)')
                db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(dict(env='SIMULATE',account='8652',baseline={},positions={},orders={},bound_at='2026-09-21T12:00:00+00:00')),));db.commit();db.close()
                records.append(dict(file=name,size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            (root/'manifest.json').write_text(json.dumps(dict(request_id='reset1',databases=records)))
            self.assertEqual(load_reset_history(root,account='8652',request_id='reset1')['orders'],[])
            with self.assertRaises(SafetyError):load_reset_history(root,account='8653',request_id='reset1')
            with self.assertRaises(SafetyError):load_reset_history(root,account='8652',request_id='other')
            with (root/'paper.sqlite').open('ab') as f:f.write(b'tamper')
            with self.assertRaises(SafetyError):load_reset_history(root,account='8652',request_id='reset1')
