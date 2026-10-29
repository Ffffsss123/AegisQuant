import unittest,copy
from trading.news_selection import opportunity_pool,extend_us_scan
from tests.test_news_opportunity import event
from tests.test_news_emergency import snap
from tests.test_trading import NOW
from trading.swing_model import VERSION

class NewsSelection(unittest.TestCase):
    def test_outside_candidates_promoted_only_with_valid_signal(self):
        pool=opportunity_pool(snap(event()),NOW)
        scan=dict(version=VERSION,complete=True,entry_verified_codes=['US.A'],candidates=[],signals={'US.A':dict(buy=True,liquid=True)})
        result=extend_us_scan(scan,pool)
        self.assertEqual(result['candidates'][0]['code'],'US.A')
        self.assertEqual(scan['candidates'],[])
        self.assertFalse(extend_us_scan(dict(scan,signals={}),pool)['candidates'])
        self.assertTrue(extend_us_scan(dict(scan,signals={}),pool)['news_pending'])
        self.assertFalse(extend_us_scan(dict(scan,signals={'US.A':dict(buy=False,liquid=True)}),pool)['candidates'])
    def test_both_markets_discovered_but_bad_health_cannot_promote(self):
        snapshot=snap(event());e=copy.deepcopy(snapshot['events'][0])
        e['id']='hk';e['codes']=['HK.00001'];e['entry_opportunity']['code']='HK.00001'
        snapshot['events'].append(e);snapshot['health']['HK.00001']=dict(ok=True,checked_at=NOW.isoformat())
        pool=opportunity_pool(snapshot,NOW)
        self.assertEqual({r['market'] for r in pool['opportunities']},{'US','HK'})
        snapshot['health']['US.A']['ok']=False
        blocked=opportunity_pool(snapshot,NOW)
        scan=dict(version=VERSION,complete=True,entry_verified_codes=['US.A'],candidates=[],signals={'US.A':dict(buy=True,liquid=True)})
        self.assertFalse(extend_us_scan(scan,blocked)['candidates'])
