import tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from trading.hk_catalog_schedule import CatalogSchedule
class HKCatalogSchedule(unittest.TestCase):
    def test_restart_retains_retry_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calls=[]
            class Controller:
                def running(self,key):return False
                def start(self,key):calls.append(key)
            now=datetime(2026,9,17,12,tzinfo=timezone.utc)
            c=Controller();CatalogSchedule(c,root).tick(now)
            s=CatalogSchedule(c,root).tick(now+timedelta(seconds=1))
            self.assertEqual(s['status'],'retry_wait');self.assertEqual(s['retries'],1)
            CatalogSchedule(c,root).tick(now+timedelta(seconds=10));self.assertEqual(len(calls),1)
            CatalogSchedule(c,root).tick(now+timedelta(seconds=302));self.assertEqual(len(calls),2)
