import json,tempfile,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from trading.simulation_controls import update_controls

class DurableControls(unittest.TestCase):
    def test_concurrent_updates_preserve_pause_and_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'controls.json'
            path.write_text(json.dumps({'stopped':True,'require_news_opportunity':True}))
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs=[pool.submit(update_controls,path,lambda s:{'capital_migration_pending':True}),
                      pool.submit(update_controls,path,lambda s:{'paused':True,'stopped':s['stopped']})]
                for job in jobs:job.result()
            state=json.loads(path.read_text())
            self.assertEqual(state,dict(stopped=True,require_news_opportunity=True,paused=True,capital_migration_pending=True))

    def test_sealed_migration_interlock_preserves_stop_and_rejects_other_plan(self):
        from unittest.mock import MagicMock
        from trading.capital import begin_staged_migration
        from trading.policy import SafetyError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'data/trading/simulation_controls.json'
            update_controls(path,lambda s:{'paused':True,'stopped':True,'require_news_opportunity':True})
            journal=MagicMock();journal.exists.return_value=True
            journal.load.return_value=dict(version=1,migration_id='m1',execution_enabled=False,stages=[{}])
            first=begin_staged_migration(root,journal,'m1')
            self.assertEqual(begin_staged_migration(root,journal,'m1'),first)
            self.assertTrue(first['stopped']);self.assertTrue(first['paused']);self.assertTrue(first['capital_migration_pending'])
            journal.load.return_value['migration_id']='m2'
            with self.assertRaises(SafetyError):begin_staged_migration(root,journal,'m2')
            self.assertEqual(json.loads(path.read_text()),first)

    def test_release_requires_all_receipts_and_fresh_check_and_keeps_pause(self):
        from trading.store import Store
        from trading.capital import seal_staged_plan,begin_staged_migration,apply_sealed_plan_locked,finish_staged_migration_locked
        from trading.policy import SafetyError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);journal=Store(root/'plan.sqlite');target=Store(root/'target.sqlite')
            try:
                stages=[(target,None,{'env':'SIMULATE','cash':500})]
                seal_staged_plan(journal,stages,'m')
                path=root/'data/trading/simulation_controls.json'
                update_controls(path,lambda s:{'paused':True,'stopped':True})
                begin_staged_migration(root,journal,'m')
                with self.assertRaisesRegex(SafetyError,'receipt incomplete'):
                    finish_staged_migration_locked(root,journal,stages,'m',lambda:True)
                apply_sealed_plan_locked(root,journal,stages,'m')
                with self.assertRaisesRegex(SafetyError,'verification failed'):
                    finish_staged_migration_locked(root,journal,stages,'m',lambda:False)
                self.assertTrue(json.loads(path.read_text())['capital_migration_pending'])
                from unittest.mock import patch
                with patch('time.monotonic',side_effect=[0,31]):
                    with self.assertRaisesRegex(SafetyError,'verification expired'):
                        finish_staged_migration_locked(root,journal,stages,'m',lambda:True)
                self.assertTrue(json.loads(path.read_text())['capital_migration_pending'])
                result=finish_staged_migration_locked(root,journal,stages,'m',lambda:True)
                self.assertFalse(result['capital_migration_pending'])
                self.assertTrue(result['paused']);self.assertTrue(result['stopped'])
            finally:target.close();journal.close()
