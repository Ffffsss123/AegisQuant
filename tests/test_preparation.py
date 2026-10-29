import unittest,tempfile
from pathlib import Path
from trading.paper_probe import PaperOnly
from trading.policy import SafetyError
from trading.cli import paper_evidence
from trading.store import Store

class Preparation(unittest.TestCase):
    def test_probe_refuses_real_binding_before_connection(self):
        b=PaperOnly.__new__(PaperOnly)
        with self.assertRaises(SafetyError):b.bind('anything','REAL')
    def test_probe_refuses_real_mutation_parameters(self):
        b=PaperOnly.__new__(PaperOnly);b.env='REAL'
        with self.assertRaises(SafetyError):b.kw()
    def test_probe_cannot_unlock_strategy_live(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'probe.db';db=Store(p);db.save(dict(env='SIMULATE',purpose='transport_probe'));db.close()
            with self.assertRaises(SafetyError):paper_evidence(p,'STOCK')
    def test_duplicate_runner_rejected_before_connection(self):
        import fcntl
        from unittest.mock import patch
        from trading.paper_probe import main
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'probe.db')
            with open(path+'.runner.lock','a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch('trading.paper_probe.PaperOnly') as broker:
                    with self.assertRaisesRegex(SafetyError,'already running'):main(['--account','paper','--db',path])
                    broker.assert_not_called()
    def test_orderbook_without_exchange_time_never_uses_local_clock(self):
        from types import SimpleNamespace
        from trading.quote_feed import exchange_time
        for stamp in ('',None,'invalid'):
            raw=SimpleNamespace(HasField=lambda k:k=='svrRecvTimeBid',svrRecvTimeBid=stamp)
            with self.assertRaisesRegex(SafetyError,'freshness cannot be verified'):exchange_time(raw,'Bid')
