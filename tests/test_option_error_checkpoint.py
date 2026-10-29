import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from trading.store import Store
from trading import option_transport as module


class OptionErrorCheckpoint(unittest.TestCase):
    def test_busy_checkpoint_preserves_original_fault_before_resuming(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'data/trading/option_transport.sqlite'
            db=Store(path)
            db.save({'paused':False,'alerts':['existing evidence']})
            with open(str(path)+'.lock','a') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
                waits=[]
                def wait(seconds):
                    waits.append(seconds)
                    if len(waits)==1:
                        self.assertFalse(db.load()['paused'])
                        fcntl.flock(held,fcntl.LOCK_UN)
                    else:
                        raise KeyboardInterrupt()
                with patch.object(module,'ROOT',root),patch.object(module,'protection_passed',side_effect=[False,True]) as protection,patch.object(module,'report') as report,patch.object(module.time,'sleep',side_effect=wait),patch.object(module,'OptionBroker') as broker:
                    with self.assertRaises(KeyboardInterrupt):module.main()
                    broker.assert_not_called()
                    self.assertEqual(protection.call_count,1)
                    self.assertEqual([c.args[0] for c in report.call_args_list],['waiting_error_checkpoint','needs_attention'])
                state=db.load()
                self.assertTrue(state['paused'])
                self.assertEqual(state['alerts'],['existing evidence','Protection validation missing or code changed'])
            db.close()
