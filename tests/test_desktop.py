import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from trading.desktop_server import Controller,ledger,Handler
from trading.store import Store

class Desktop(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'data/desktop'
        self.patches=[patch('trading.desktop_server.ROOT',self.root),patch('trading.desktop_server.DATA',self.data)]
        for p in self.patches:p.start()
        self.c=Controller(initial_mode='simulation')
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def test_missing_data_empty_not_fake_equity(self):
        s=self.c.status();self.assertEqual(s['weeks'],[]);self.assertEqual(s['marks'],[]);self.assertFalse(s['paper_bound']);self.assertFalse(s['live_enabled'])
    def test_arbitrary_command_rejected(self):
        for key in ['enable','sell','REAL','../../shell','rm -rf']:
            with self.assertRaises(ValueError):self.c.start(key)
    def test_real_binding_rejects_worker(self):
        db=Store(self.root/'data/trading/paper.sqlite');db.save(dict(env='REAL'));db.close()
        with patch('subprocess.Popen') as popen:
            with self.assertRaises(ValueError):self.c.start('scan')
            popen.assert_not_called()
    def test_sim_account_id_not_returned(self):
        db=Store(self.root/'data/trading/paper.sqlite');db.save(dict(env='SIMULATE',account='SECRET',positions={},marks=[],alerts=[]));db.close()
        self.assertNotIn('SECRET',json.dumps(self.c.status()))
    def test_probe_requires_binding(self):
        with self.assertRaises(ValueError):self.c.start('probe')
    def test_no_duplicate_process(self):
        child=Mock();child.poll.return_value=None;self.c.jobs['scan']=child
        with patch('subprocess.Popen') as popen:
            self.assertIn('已经运行',self.c.start('scan'));popen.assert_not_called()
    def test_pause_real_rejected_without_mutation(self):
        p=self.root/'data/trading/paper.sqlite';db=Store(p);db.save(dict(env='REAL',paused=False));db.close()
        with self.assertRaises(ValueError):self.c.pause_local()
        self.assertFalse(ledger(p)['paused'])
    def test_pause_persisted_before_remote_worker(self):
        p=self.root/'data/trading/probe.sqlite';db=Store(p);db.save(dict(env='SIMULATE',paused=False,alerts=[]));db.close()
        with patch.object(self.c,'start',return_value='worker pending'):
            self.c.pause_local()
        self.assertTrue(ledger(p)['paused'])
    def test_pause_preserves_news_reset_and_latched_stop(self):
        p=self.root/'data/trading/simulation_controls.json';p.parent.mkdir(parents=True,exist_ok=True)
        previous=dict(require_news_opportunity=True,reset_pending=True,reset_requested_at='request-time',stopped=True)
        p.write_text(json.dumps(previous))
        with patch.object(self.c,'start',return_value='worker pending'):
            self.c.pause_local()
        actual=json.loads(p.read_text())
        for key,value in previous.items():self.assertEqual(actual[key],value)
        self.assertTrue(actual['paused'])
    def test_auth_rejects_browser_origin_and_wrong_host(self):
        h=Handler.__new__(Handler);h.server=Mock(server_port=8765,token='secret')
        h.headers={'Host':'127.0.0.1:8765','X-Local-Token':'secret'};self.assertTrue(h.authorized())
        h.headers['Origin']='https://evil.example';self.assertFalse(h.authorized())
        h.headers.pop('Origin');h.headers['Host']='evil.example';self.assertFalse(h.authorized())
    def test_auth_rejects_missing_token(self):
        h=Handler.__new__(Handler);h.server=Mock(server_port=8765,token='secret');h.headers={'Host':'127.0.0.1:8765'}
        self.assertFalse(h.authorized())
    def test_public_news_does_not_require_sim_account(self):
        db=Store(self.root/'data/trading/paper.sqlite');db.save(dict(env='REAL'));db.close()
        with patch('subprocess.Popen') as popen:
            self.c.start('news')
            self.assertEqual(popen.call_args.args[0][-2:],['-m','trading.desktop_news'])
    def test_supervisor_starts_only_news_and_retries(self):
        stop=Mock();stop.is_set.side_effect=[False,False,True]
        with patch.object(self.c,'start',side_effect=[OSError('offline'),'started']) as start:
            self.c.supervise_news(stop)
        self.assertEqual([c.args for c in start.call_args_list],[('news',),('news',)])
        self.assertEqual(stop.wait.call_count,2)
    def test_existing_news_process_survives_backend_restart(self):
        (self.data/'news_process.json').write_text(json.dumps({'pid':123}))
        with patch('trading.desktop_server.alive',return_value=True),patch('subprocess.Popen') as popen:
            self.c.start('news');popen.assert_not_called()
    def test_existing_scan_survives_backend_restart_without_duplicate(self):
        (self.data/'jobs.json').write_text(json.dumps({'scan':{'pid':123}}))
        with patch('trading.desktop_server.alive',return_value=True),patch('subprocess.Popen') as popen:
            self.c.start('scan');popen.assert_not_called()
    def test_news_error_keeps_snapshot_and_allows_retry(self):
        from trading.desktop_news import collect_once
        with patch('trading.desktop_news.watched_codes',return_value=['US.SPY']),patch('trading.desktop_news.refresh_live',side_effect=[OSError('offline'),{'health':{}}]),patch('builtins.print'):
            self.assertFalse(collect_once());self.assertTrue(collect_once())
    def test_news_watches_candidates_and_held_symbols(self):
        from trading.desktop_news import watched_codes
        scan={'candidates':[{'code':'US.NVDA'},{'code':'HK.00700'}]}
        with patch('trading.desktop_news.read_json',return_value=scan),patch('trading.desktop_news.ledger',return_value={'positions':{'US.MSFT':{'qty':1},'US.F':{'qty':0}}}):
            self.assertEqual(watched_codes(),['HK.00700','US.AAPL','US.MSFT','US.NVDA','US.SPY'])
