import unittest,tempfile,sqlite3,json
from pathlib import Path
from trading.alert_history import AlertHistory
class Timeline(unittest.TestCase):
    def test_incremental_times_duplicates_recovery_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'s.db';c=sqlite3.connect(p);c.execute('create table events(id integer primary key,ts text,kind text,body text)')
            a='Strategy runtime error: Disconnect';b='Valuation unavailable: 网络中断'
            def add(t,m):c.execute('insert into events(ts,kind,body) values(?,?,?)',(t,'alert',json.dumps(dict(message=m))));c.commit()
            add('2026-09-16T01:00:00+00:00',a);add('2026-09-16T02:00:00+00:00',b)
            h=AlertHistory();r=h.records(p,[a,b,a],'test');self.assertEqual(len(r),1);self.assertEqual(r[0]['count'],2)
            self.assertEqual(r[0]['first_seen'],'2026-09-16T01:00:00+00:00');self.assertEqual(h.records(p,[a,b],'test')[0]['count'],2)
            add('2026-09-16T03:00:00+00:00',a);r=h.records(p,[a,b],'test');self.assertEqual(r[0]['count'],3);self.assertEqual(r[0]['last_seen'],'2026-09-16T03:00:00+00:00')
            self.assertEqual(h.records(p,[],'test'),[])
            self.assertIsNone(h.records(p,['User paused entries'],'test')[0]['last_seen']);c.close()
    def test_missing_database_has_no_fabricated_time(self):
        with tempfile.TemporaryDirectory() as d:
            r=AlertHistory().records(Path(d)/'missing',['error'],'test')[0]
            self.assertIsNone(r['first_seen']);self.assertIsNone(r['last_seen'])
    def test_recovery_requires_matching_newer_evidence(self):
        h=AlertHistory();a='Valuation unavailable: Stale intraday mark';b='Position mismatch / manual activity / corporate action'
        history={a:dict(first_seen='01',last_seen='03',count=2),b:dict(first_seen='01',last_seen='01',count=1)}
        rows=h._group(history,[a,b],'test',{'valuation':'04'})
        self.assertTrue(next(r for r in rows if a in r['details'])['resolved'])
        self.assertFalse(next(r for r in rows if b in r['details'])['resolved'])
        history[a]['last_seen']='05'
        self.assertFalse(h._group(history,[a],'test',{'valuation':'04'})[0]['resolved'])
    def test_connection_needs_all_queries_after_failure(self):
        a='Strategy runtime error: Disconnect';h=AlertHistory();history={a:dict(last_seen='03')}
        success={'broker_orders':'04','broker_history_orders':'04'}
        self.assertFalse(h._group(history,[a],'test',success)[0]['resolved'])
        success['broker_positions']='05'
        self.assertTrue(h._group(history,[a],'test',success)[0]['resolved'])
