import unittest,tempfile,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from trading.simulation_history import load_empty_hk_history
from trading.policy import SafetyError

class EmptyHistory(unittest.TestCase):
    def test_intraday_overlap_identity_and_tampering(self):
        now=datetime(2026,9,22,6,tzinfo=timezone.utc);stamp=now.isoformat()
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);rows=[]
            for name in ['accounts','history_orders','orders','funds','positions']:
                rows.append(dict(query=name,account='8653',env='SIMULATE',return_code=0,
                    started_at=stamp,completed_at=stamp,request=dict(acc_id=8653,trd_env='SIMULATE'),response=[]))
            rows[0]['response']=[dict(acc_id=8653,trd_env='SIMULATE',trdmarket_auth=['HK'])]
            rows[1]['request'].update(start='2026-06-25',end='2026-09-22')
            def write():
                raw='\n'.join(json.dumps(r) for r in rows).encode();(base/'raw.jsonl').write_bytes(raw)
                (base/'manifest.json').write_text(json.dumps(dict(account='8653',env='SIMULATE',query_start='2026-06-25',query_end='2026-09-22',observed_at=stamp,raw_sha256=hashlib.sha256(raw).hexdigest())))
            def read(account='8653'):
                return load_empty_hk_history(base,account=account,history_start='2026-06-25',now=now)
            write();self.assertEqual(read()['next_query_start'],'2026-09-22')
            from trading.simulation_history import bound_hk_history_start
            stock=dict(env='SIMULATE',hk_account='8653',hk_history_start='2026-06-25',
                       hk_empty_history=dict(directory=str(base),raw_sha256=read()['raw_sha256']))
            self.assertEqual(bound_hk_history_start(stock,account='8653',now=now),'2026-09-22')
            with self.assertRaisesRegex(SafetyError,'history start required'):
                bound_hk_history_start(dict(stock,hk_history_start=None),account='8653',now=now)
            with self.assertRaisesRegex(SafetyError,'digest mismatch'):
                bound_hk_history_start(dict(stock,hk_empty_history=dict(directory=str(base),raw_sha256='bad')),account='8653',now=now)
            with self.assertRaises(SafetyError):read('9999')
            (base/'raw.jsonl').write_bytes(b'changed')
            with self.assertRaises(SafetyError):read()
            rows[1]['response']=[dict(order_id='unknown')];write()
            with self.assertRaises(SafetyError):read()
