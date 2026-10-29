import unittest
from trading.simulation_ownership import ownership
from trading.policy import SafetyError

class Ownership(unittest.TestCase):
    def state(self, code, oid):
        return dict(env='SIMULATE',account='1',baseline={},positions={code:dict(qty=1)},orders={'tag':dict(id=oid)})
    def test_union_keeps_separate_owners(self):
        r=ownership({'stock':self.state('US.A','1'),'option':self.state('US.OPT','2')},'1')
        self.assertEqual(r['positions'],{'US.A':1,'US.OPT':1})
        self.assertEqual(r['order_owners']['2'],'option')
    def test_duplicate_order_or_position_rejected(self):
        for code,oid in [('US.B','1'),('US.A','2')]:
            with self.assertRaises(SafetyError):ownership({'a':self.state('US.A','1'),'b':self.state(code,oid)},'1')
    def test_real_wrong_account_and_external_inventory_rejected(self):
        for patch in [dict(env='REAL'),dict(account='2'),dict(baseline={'US.X':1})]:
            s=self.state('US.A','1');s.update(patch)
            with self.assertRaises(SafetyError):ownership({'a':s},'1')
    def test_unknown_identity_and_invalid_quantity_rejected(self):
        for qty in [True,float('nan'),-1]:
            s=self.state('US.A','1');s['positions']['US.A']['qty']=qty
            with self.assertRaises(SafetyError):ownership({'a':s},'1')
        s=self.state('US.A',None)
        with self.assertRaises(SafetyError):ownership({'a':s},'1')

    def test_missing_inventory_or_orders_is_not_empty_account(self):
        for key in ('baseline','positions','orders'):
            s=self.state('US.A','1');del s[key]
            with self.assertRaises(SafetyError):ownership({'a':s},'1')

    def test_broker_union_preserves_peer_holdings(self):
        from trading.simulation_ownership import reconcile_positions
        ledgers={'a':self.state('US.A','1'),'b':self.state('US.B','2')}
        result=reconcile_positions(ledgers,'1',[dict(code='US.A',qty=1),dict(code='US.B',qty=1)])
        self.assertEqual(result['position_owners']['US.B'],'b')

    def test_broker_inventory_must_match_complete_union(self):
        from trading.simulation_ownership import reconcile_positions
        for rows in [[],[dict(code='US.A',qty=2)],
                     [dict(code='US.A',qty=1),dict(code='US.X',qty=1)],
                     [dict(code='US.A',qty=1),dict(code='US.A',qty=0)],
                     [dict(code='US.A',qty=float('nan'))],
                     [dict(code='US.A',qty=-1)],None,[{}]]:
            with self.subTest(rows=rows), self.assertRaises(SafetyError):
                reconcile_positions({'a':self.state('US.A','1')},'1',rows)

    def test_order_union_checks_booked_fills_and_status(self):
        from trading.simulation_ownership import reconcile_orders
        s=self.state('US.A','1')
        s['orders']['tag'].update(code='US.A',side='BUY',status='FILLED_ALL',qty=1,booked_qty=1)
        row=dict(id='1',code='US.A',side='BUY',status='FILLED_ALL',qty=1,filled=1)
        self.assertEqual(reconcile_orders({'a':s},'1',[row])['order_owners'],{'1':'a'})
        for patch in [dict(id='unknown'),dict(code='US.B'),dict(side='SELL'),
                      dict(status='SUBMITTED'),dict(qty=2),dict(filled=0),dict(filled=True)]:
            with self.subTest(patch=patch),self.assertRaises(SafetyError):
                reconcile_orders({'a':s},'1',[dict(row,**patch)])
        for rows in [[],[row,row],None]:
            with self.assertRaises(SafetyError):reconcile_orders({'a':s},'1',rows)

    def test_scoped_flat_retains_stock_and_rejects_unknown_peer_activity(self):
        from trading.simulation_ownership import verify_owner_flat
        stock=self.state('US.A','1')
        stock['orders']['tag'].update(code='US.A',side='BUY',qty=1,booked_qty=1,status='FILLED_ALL',fee_final=True)
        option=dict(env='SIMULATE',account='1',baseline={},positions={},orders={})
        orders=[dict(id='1',code='US.A',side='BUY',qty=1,filled=1,status='FILLED_ALL')]
        positions=[dict(code='US.A',qty=1)]
        states={'stock':stock,'option':option}
        self.assertEqual(verify_owner_flat(states,'1','option',orders,positions)['positions'],{'US.A':1})
        with self.assertRaises(SafetyError):verify_owner_flat(states,'1','stock',orders,positions)
        with self.assertRaises(SafetyError):verify_owner_flat(states,'1','option',orders+[dict(orders[0],id='unknown')],positions)
        option['orders']['new']=dict(id='2',code='US.OPT',side='BUY',qty=1,booked_qty=0,status='SUBMITTED',fee_final=False)
        with self.assertRaises(SafetyError):
            verify_owner_flat(states,'1','option',orders+[dict(id='2',code='US.OPT',side='BUY',qty=1,filled=0,status='SUBMITTED')],positions)
