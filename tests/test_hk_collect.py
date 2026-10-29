import tempfile,unittest,json
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from trading.hk_collect import collect
from trading.policy import SafetyError

class HKCollect(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=datetime(2026,9,18,tzinfo=timezone.utc)
        self.catalog=dict(source_date='2026-09-18',rows={'HK.00001':{'code':'HK.00001'}})
    def run_collect(self,history,held=()):
        result=dict(date='2026-09-17',complete=True,errors={},orders_enabled=False,candidates=[],signals={},scope='Reviewed only')
        with patch('trading.hk_collect.reviewed_class',return_value={}),patch('trading.hk_collect.screen',return_value=result):
            return collect(self.root,self.catalog,[],'2026-09-17',{},self.now,history,lambda c:[dict(code=c)],held=held)
    def test_complete_publishes_separately_from_progress(self):
        r=self.run_collect(lambda c:[dict(code=c,time_key='2026-09-17 00:00:00')])
        self.assertTrue(r['complete']);self.assertTrue((self.root/'scan.json').exists())
        self.assertEqual(json.loads((self.root/'scan_progress.json').read_text())['status'],'complete')
    def test_provider_failure_preserves_previous(self):
        (self.root/'scan.json').write_text('previous')
        def fail(c):raise SafetyError('Connection failed')
        r=self.run_collect(fail);self.assertFalse(r['complete'])
        self.assertEqual((self.root/'scan.json').read_text(),'previous')
    def test_wrong_history_identity_cannot_publish(self):
        r=self.run_collect(lambda c:[dict(code='HK.99999',time_key='2026-09-17')])
        self.assertFalse(r['complete']);self.assertFalse((self.root/'scan.json').exists())
    def test_held_removed_symbol_still_fetched(self):
        seen=[]
        def history(c):seen.append(c);return [dict(code=c,time_key='2026-09-17')]
        self.run_collect(history,held=['HK.00002'])
        self.assertEqual(seen,['HK.00001','HK.00002'])
