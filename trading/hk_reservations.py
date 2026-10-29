"""Offline durable reservation journal, not connected to live admission paths.

Caller supplies one reconciled USD capacity *before* reservations in this journal,
and must serialize all markets with the shared admission lock before integration.
Never expire holds by time: ambiguous broker outcomes retain their full reservation.
"""
import sqlite3,json,re
from .hk_policy import amount
from .policy import SafetyError

class Reservations:
    def __init__(self,path,portfolio_id):
        if not portfolio_id:raise SafetyError('Portfolio identity required')
        self.db=sqlite3.connect(str(path),timeout=5,isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), portfolio TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO identity VALUES(1,?)',(portfolio_id,))
        if self.db.execute('SELECT portfolio FROM identity').fetchone()[0]!=portfolio_id:
            self.db.close();raise SafetyError('Reservation portfolio mismatch')
        self.db.execute('CREATE TABLE IF NOT EXISTS holds (key TEXT PRIMARY KEY, amount TEXT NOT NULL, state TEXT NOT NULL, evidence TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settled_fills (key TEXT PRIMARY KEY, debit TEXT NOT NULL, qty TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS reservation_intents (key TEXT PRIMARY KEY, amount TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS reservation_identity (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS closed_positions (key TEXT PRIMARY KEY, proceeds TEXT NOT NULL, qty TEXT NOT NULL, pnl TEXT NOT NULL, order_ids TEXT NOT NULL, evidence TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS closing_orders (order_id TEXT PRIMARY KEY, position_key TEXT NOT NULL)')
        # Old HELD amounts still represent their original request. A legacy
        # COMMITTED amount does not: never invent its original reservation.
        self.db.execute("INSERT OR IGNORE INTO reservation_intents SELECT key,amount FROM holds WHERE state='HELD'")
    def close(self):self.db.close()
    def slot_records(self,account,owner):
        """Caller holds admission lock; unmapped legacy holds block admission."""
        from .simulation_slots import occupied_slots
        records=[]
        rows=self.db.execute('SELECT h.key,h.state,i.body FROM holds h LEFT JOIN reservation_identity i ON h.key=i.key').fetchall()
        for key,state,body in rows:
            if state not in ('HELD','COMMITTED','RELEASED'):
                raise SafetyError('Unknown HK reservation state')
            if state=='RELEASED':continue
            if body is None:raise SafetyError('Active HK hold lacks durable instrument identity')
            try:identity=json.loads(body)
            except (TypeError,ValueError) as exc:raise SafetyError('Invalid HK reservation identity') from exc
            if (not isinstance(identity,dict) or identity.get('tag')!=key
                or identity.get('account')!=account or identity.get('env')!='SIMULATE'
                or identity.get('side')!='BUY'):
                raise SafetyError('HK reservation identity mismatch')
            records.append(dict(category='HK_STOCK',code=identity.get('code'),account=account,owner=owner,
                                state='HELD' if state=='COMMITTED' else 'ENTRY_UNKNOWN'))
        occupied_slots(records,env='SIMULATE')
        return records
    @classmethod
    def read_slots(cls,path,portfolio_id,account,owner='HK'):
        """Read existing journal only; caller retains the shared admission lock."""
        from pathlib import Path
        journal=cls.__new__(cls)
        journal.db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            journal.db.execute('BEGIN')
            row=journal.db.execute('SELECT portfolio FROM identity WHERE id=1').fetchone()
            if not row or not portfolio_id or row[0]!=portfolio_id:
                raise SafetyError('HK slot portfolio mismatch')
            return journal.slot_records(account,owner)
        finally:journal.db.close()

    def committed_and_held(self):
        """Single representation of capital; committed fills replace their hold."""
        return sum((amount(r[0]) for r in self.db.execute("SELECT amount FROM holds WHERE state!='RELEASED'")),amount(0))
    def settle_fill(self,key,*,debit_usd,filled_qty,broker_status,reconciled,evidence):
        """Offline terminal-fill reconciliation. Debit includes buy fees exactly once.

        Retain actual cost even if it exceeds the reservation; subsequent admission
        must see that overrun. Open/ambiguous orders keep their original full hold.
        Closing requires a separate fully reconciled flat-position transition.
        """
        debit,qty=amount(debit_usd),amount(filled_qty)
        if (reconciled is not True or not isinstance(evidence,str) or not evidence.strip()
            or debit<=0 or qty<=0 or qty!=qty.to_integral_value()
            or broker_status not in ('FILLED_ALL','CANCELLED_PART')):
            raise SafetyError('Reconciled terminal fill evidence required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT amount,state FROM holds WHERE key=?',(key,)).fetchone()
            if not row or row[1]=='RELEASED':raise SafetyError('Missing active reservation')
            prior=self.db.execute('SELECT debit,qty,status FROM settled_fills WHERE key=?',(key,)).fetchone()
            if prior:
                if (amount(prior[0]),amount(prior[1]),prior[2])!=(debit,qty,broker_status):
                    raise SafetyError('Conflicting terminal fill reconciliation')
            else:
                if row[1]!='HELD':raise SafetyError('Invalid reservation transition')
                self.db.execute('INSERT INTO settled_fills VALUES(?,?,?,?,?)',(key,str(debit),str(qty),broker_status,evidence))
                self.db.execute("UPDATE holds SET amount=?,state='COMMITTED',evidence=? WHERE key=?",(str(debit),evidence,key))
            self.db.commit()
            return dict(key=key,state='COMMITTED',amount_usd=str(debit),orders_enabled=False)
        except Exception:self.db.rollback();raise
    def reserve(self,key,usd,capacity_usd,*,identity=None):
        body=None
        if identity is not None:
            if (not isinstance(identity,dict) or identity.get('tag')!=key or identity.get('env')!='SIMULATE'
                or identity.get('side')!='BUY' or not isinstance(identity.get('account'),str)
                or not identity['account'].strip() or identity['account']!=identity['account'].strip()
                or not re.fullmatch(r'HK\.[0-9]{5}',str(identity.get('code','')))):
                raise SafetyError('Invalid HK reservation identity')
            body=json.dumps({k:identity[k] for k in ('tag','env','account','side','code')},sort_keys=True)
        value,capacity=amount(usd),amount(capacity_usd)
        if not key or not 0<value<=500 or not 0<=capacity<=500:raise SafetyError('Invalid shared reservation')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT amount,state FROM holds WHERE key=?',(key,)).fetchone()
            if row:
                prior=self.db.execute('SELECT body FROM reservation_identity WHERE key=?',(key,)).fetchone()
                if (prior[0] if prior else None)!=body:raise SafetyError('Reservation identity cannot be changed or inferred')
                intent=self.db.execute('SELECT amount FROM reservation_intents WHERE key=?',(key,)).fetchone()
                if not intent or amount(intent[0])!=value or row[1]=='RELEASED':raise SafetyError('Reservation key cannot be reused')
                self.db.commit();return dict(key=key,amount_usd=row[0],state=row[1],orders_enabled=False)
            held=sum((amount(r[0]) for r in self.db.execute("SELECT amount FROM holds WHERE state!='RELEASED'")),amount(0))
            if held+value>capacity:raise SafetyError('Shared capacity exhausted')
            self.db.execute('INSERT INTO holds VALUES(?,?,?,NULL)',(key,str(value),'HELD'))
            self.db.execute('INSERT INTO reservation_intents VALUES(?,?)',(key,str(value)))
            if body is not None:self.db.execute('INSERT INTO reservation_identity VALUES(?,?)',(key,body))
            self.db.commit();return dict(key=key,amount_usd=str(value),state='HELD',orders_enabled=False)
        except Exception:self.db.rollback();raise
    def settle_closed_position(self,key,*,net_proceeds_usd,sold_qty,order_ids,
                               position_flat,no_pending_orders,reconciled,evidence):
        """Offline full-close settlement, using the same fixed FX as entry.

        Proceeds are cumulative actual sells minus all exit fees. Caller must
        verify every order is terminal and attributable to this entry. Partial
        or uncertain exits keep the entire committed debit reserved.
        """
        proceeds,qty=amount(net_proceeds_usd),amount(sold_qty)
        if (position_flat is not True or no_pending_orders is not True or reconciled is not True
            or not isinstance(evidence,str) or not evidence.strip()
            or qty<=0 or qty!=qty.to_integral_value()
            or not isinstance(order_ids,(list,tuple)) or not order_ids
            or any(not isinstance(v,str) or not v.strip() for v in order_ids)
            or len(set(order_ids))!=len(order_ids)):
            raise SafetyError('Reconciled full-close evidence required')
        ids=json.dumps(sorted(order_ids))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT proceeds,qty,pnl,order_ids FROM closed_positions WHERE key=?',(key,)).fetchone()
            if prior:
                if (amount(prior[0]),amount(prior[1]),prior[3])!=(proceeds,qty,ids):
                    raise SafetyError('Conflicting close reconciliation')
                pnl=amount(prior[2])
            else:
                entry=self.db.execute('SELECT debit,qty FROM settled_fills WHERE key=?',(key,)).fetchone()
                hold=self.db.execute('SELECT state FROM holds WHERE key=?',(key,)).fetchone()
                if not entry or not hold or hold[0]!='COMMITTED' or amount(entry[1])!=qty:
                    raise SafetyError('Close does not match committed entry quantity')
                for oid in order_ids:
                    if self.db.execute('SELECT 1 FROM closing_orders WHERE order_id=?',(oid,)).fetchone():
                        raise SafetyError('Closing order already attributed')
                pnl=proceeds-amount(entry[0])
                self.db.execute('INSERT INTO closed_positions VALUES(?,?,?,?,?,?)',(key,str(proceeds),str(qty),str(pnl),ids,evidence))
                self.db.executemany('INSERT INTO closing_orders VALUES(?,?)',[(oid,key) for oid in order_ids])
                self.db.execute("UPDATE holds SET state='RELEASED',evidence=? WHERE key=?",(evidence,key))
            self.db.commit()
            return dict(key=key,state='RELEASED',realized_pnl_usd=str(pnl),orders_enabled=False)
        except Exception:self.db.rollback();raise
    def realized_pnl(self):
        return sum((amount(r[0]) for r in self.db.execute('SELECT pnl FROM closed_positions')),amount(0))
    def risk_totals(self,valuations):
        """Consistent offline totals; caller must verify fresh broker valuations.

        valuations maps every COMMITTED entry key to its full remaining market
        value in USD and conservative exit fee reserve. Partial exits retain
        full committed capital and must not be published as reconciled values.
        Entry debit already includes buy fees; closed PnL already includes both
        sides' fees. Neither may have those fees subtracted a second time.
        """
        if not isinstance(valuations,dict):raise SafetyError('Explicit position valuations required')
        self.db.execute('BEGIN')
        try:
            rows=self.db.execute("SELECT key,amount,state FROM holds WHERE state!='RELEASED'").fetchall()
            committed={key:amount(value) for key,value,state in rows if state=='COMMITTED'}
            if set(valuations)!=set(committed):raise SafetyError('Valuation and committed position keys differ')
            if any(state not in ('HELD','COMMITTED') for _,_,state in rows):raise SafetyError('Unknown reservation state')
            occupied=sum((amount(value) for _,value,_ in rows),amount(0));unrealized=amount(0)
            committed_debit=sum(committed.values(),amount(0))
            held=sum((amount(value) for _,value,state in rows if state=='HELD'),amount(0))
            exit_fees=amount(0)
            for key,debit in committed.items():
                row=valuations[key]
                value,fee=amount(row.get('market_value_usd')),amount(row.get('exit_fee_reserve_usd'))
                if value<0 or fee<=0:raise SafetyError('Nonnegative value and positive exit fees required')
                occupied+=fee;exit_fees+=fee;unrealized+=value-debit-fee
            realized=self.realized_pnl()
            self.db.commit()
            return dict(occupied_usd=str(occupied),realized_net_usd=str(realized),
                        committed_debit_usd=str(committed_debit),held_usd=str(held),exit_fee_reserve_usd=str(exit_fees),
                        unrealized_net_usd=str(unrealized),
                        pending_or_unknown=any(state=='HELD' for _,_,state in rows),orders_enabled=False)
        except Exception:self.db.rollback();raise
    def release_unfilled(self,key,*,broker_status,filled_qty,reconciled,evidence):
        if (reconciled is not True or not isinstance(evidence,str) or not evidence.strip() or amount(filled_qty)!=0
            or broker_status not in ('CANCELLED_ALL','FAILED','DISABLED','DELETED','SUBMIT_FAILED')):
            raise SafetyError('Unfilled terminal broker evidence required to release')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT state FROM holds WHERE key=?',(key,)).fetchone()
            if not row:raise SafetyError('Unknown reservation')
            if row[0]=='COMMITTED':raise SafetyError('Filled capital cannot be released as unfilled')
            self.db.execute("UPDATE holds SET state='RELEASED',evidence=? WHERE key=?",(str(evidence),key))
            self.db.commit()
        except Exception:self.db.rollback();raise
