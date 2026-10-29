import unittest
from trading.simulation_budget import budget_view, SLEEVES
from trading.policy import SafetyError

class Budget(unittest.TestCase):
    def rows(self):
        return {s:dict(initial=500,cash=500,equity=500,deployed=0,reserved=0,exit_fees=0) for s in SLEEVES}
    def test_independent_cash_and_reservations(self):
        r=self.rows();r['US_OPTION'].update(cash=70,deployed=430,reserved=20,exit_fees=5)
        v=budget_view(r,env='SIMULATE',loss_latched=False)
        self.assertEqual(v['available'],dict(US_STOCK=500,US_OPTION=45,HK=500))
    def test_combined_loss_boundary_and_profit_ceiling(self):
        r=self.rows();r['US_STOCK'].update(cash=600,equity=600)
        self.assertEqual(budget_view(r,env='SIMULATE',loss_latched=False)['available']['US_STOCK'],500)
        r['HK']['equity']=300
        self.assertTrue(budget_view(r,env='SIMULATE',loss_latched=False)['entry_block'])
        self.assertEqual(sum(budget_view(r,env='SIMULATE',loss_latched=False)['available'].values()),0)
    def test_incomplete_invalid_and_real_rejected(self):
        for key,value in [('cash',-1),('initial',1000),('equity',float('nan')),('reserved',True)]:
            r=self.rows();r['HK'][key]=value
            with self.assertRaises(SafetyError):budget_view(r,env='SIMULATE',loss_latched=False)
        with self.assertRaises(SafetyError):budget_view(self.rows(),env='REAL',loss_latched=False)
        r=self.rows();del r['US_OPTION']
        with self.assertRaises(SafetyError):budget_view(r,env='SIMULATE',loss_latched=False)

    def test_loss_latch_survives_equity_recovery(self):
        r=self.rows();r['HK']['equity']=400
        halted=budget_view(r,env='SIMULATE',loss_latched=False)
        r['HK']['equity']=500
        recovered=budget_view(r,env='SIMULATE',loss_latched=halted['loss_latched'])
        self.assertEqual(recovered['combined_loss'],0)
        self.assertTrue(recovered['entry_block'])
        self.assertEqual(sum(recovered['available'].values()),0)
        for invalid in (None,0,'false'):
            with self.assertRaises(SafetyError):
                budget_view(r,env='SIMULATE',loss_latched=invalid)
