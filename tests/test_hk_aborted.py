import unittest
from trading.hk_reservations import Reservations
from trading.hk_aborted import recover
from trading.policy import SafetyError
import test_hk_transport as fixture

class HKAborted(unittest.TestCase):
    setUp=fixture.HKTransportTests.setUp
    tearDown=fixture.HKTransportTests.tearDown
    def test_only_proven_failure_releases_and_prevents_reuse(self):
        r=Reservations(self.root/'holds.sqlite','portfolio')
        tag=self.inputs['tag'];r.reserve(tag,100,500)
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,lambda req:False)
        stock=dict(env='SIMULATE',hk_account='8653',portfolio_id='portfolio')
        self.assertEqual(recover(self.journal,r,stock,self.root),[tag])
        self.assertEqual(r.committed_and_held(),0)
        self.assertEqual(recover(self.journal,r,stock,self.root),[])
        with self.assertRaises(SafetyError):self.transport.submit(self.inputs,lambda req:True)
        self.assertEqual(self.calls,[]);r.close()
    def test_unproven_orphan_retained(self):
        r=Reservations(self.root/'holds.sqlite','portfolio');r.reserve('unknown',100,500)
        self.assertEqual(recover(self.journal,r,dict(env='SIMULATE',hk_account='8653',portfolio_id='portfolio'),self.root),[])
        self.assertEqual(r.committed_and_held(),100);r.close()
    def test_account_mismatch_retains(self):
        r=Reservations(self.root/'holds.sqlite','portfolio');r.reserve('unknown',100,500)
        with self.assertRaises(SafetyError):recover(self.journal,r,dict(env='SIMULATE',hk_account='other',portfolio_id='portfolio'),self.root)
        self.assertEqual(r.committed_and_held(),100);r.close()
