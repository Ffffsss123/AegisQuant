import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from trading.sdk_read_budget import spaced_read
from trading.shared_capital import admission_lock
from trading.policy import SafetyError

class ReadBudget(unittest.TestCase):
    def test_hk_funds_and_us_cash_share_spacing_but_placement_is_not_delayed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('trading.sdk_read_budget.time.time',return_value=100),patch('trading.sdk_read_budget.time.sleep') as sleep:
                with spaced_read('cash',root=root):pass
                sleep.assert_not_called()
                with spaced_read('funds',root=root):pass
                sleep.assert_called_once_with(3.1)
                sleep.reset_mock()
                with spaced_read('place',root=root):pass
                sleep.assert_not_called()
    def test_reuse_requires_same_thread_process_and_exact_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(SafetyError):
                with admission_lock(root,reuse=True):pass
            with admission_lock(root):
                with admission_lock(root,reuse=True):pass
                with self.assertRaises(SafetyError):
                    with admission_lock(root/'other',reuse=True):pass
            with self.assertRaises(SafetyError):
                with admission_lock(root,reuse=True):pass
