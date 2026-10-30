import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from trading.desktop_server import Controller
from trading.store import Store

class WorkspaceMode(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'data/desktop'
        self.patches=[patch('trading.desktop_server.ROOT',self.root),patch('trading.desktop_server.DATA',self.data)]
        for p in self.patches:p.start()
        self.c=Controller()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def state(self,name,env='SIMULATE',**fields):
        p=self.root/'data/trading'/name;db=Store(p)
        try:db.save(dict(dict(env=env,positions={},orders={},queue=[]),**fields))
        finally:db.close()
    def test_default_live_rejects_every_simulation_job_without_launch(self):
        from trading.workspace_mode import SIMULATION_JOBS
        self.assertEqual(self.c.mode(),'live')
        with patch('subprocess.Popen') as launch:
            for key in SIMULATION_JOBS:
                with self.assertRaisesRegex(ValueError,'simulation'):self.c.start(key)
            launch.assert_not_called()
    def test_mode_switch_does_not_start_or_resume_anything(self):
        path=self.root/'data/trading/simulation_controls.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(paused=True,stopped=True,loss_latched=True,unknown='retain')))
        original=path.read_bytes()
        with patch('subprocess.Popen') as launch:
            self.c.switch_mode('simulation');self.c.switch_mode('live');launch.assert_not_called()
        self.assertEqual(path.read_bytes(),original)
    def test_new_controller_observes_latest_persisted_permission(self):
        other=Controller();self.c.switch_mode('simulation')
        self.assertEqual(other.mode(),'simulation')
        other.switch_mode('live');self.assertEqual(self.c.mode(),'live')
    def test_simulation_mode_blocks_real_account_and_private_live_actions(self):
        self.c.switch_mode('simulation')
        with patch('subprocess.Popen') as launch:
            for key in ('account','live_runner'):
                with self.assertRaises(ValueError):self.c.start(key)
            for action in ('prepare','enable','order','maintain','template','refresh_fx'):
                with self.assertRaises(ValueError):self.c.live_action({'action':action})
            launch.assert_not_called()
        self.assertFalse((self.root/'data/trading/live/requests').exists())
    def test_simulation_holdings_allow_live_and_preserve_ledger(self):
        self.c.switch_mode('simulation');self.state('paper.sqlite',positions={'US.A':{'qty':1}})
        self.c.switch_mode('live')
        self.assertEqual(self.c.mode(),'live')
        from trading.desktop_server import ledger
        self.assertEqual(ledger(self.root/'data/trading/paper.sqlite')['positions']['US.A']['qty'],1)
    def test_simulation_unknown_orders_and_queue_allow_live_without_mutation(self):
        from trading.desktop_server import ledger
        self.c.switch_mode('simulation')
        for fields in ({'orders':{'a':{'status':'UNKNOWN'}}},{'queue':[{'side':'BUY','code':'US.A'}]}):
            self.state('paper.sqlite',**fields)
            before=ledger(self.root/'data/trading/paper.sqlite')
            self.c.switch_mode('live');self.assertEqual(self.c.mode(),'live')
            self.assertEqual(ledger(self.root/'data/trading/paper.sqlite'),before)
            self.c.switch_mode('simulation')
    def test_running_simulation_worker_allows_live_and_is_not_stopped(self):
        self.c.switch_mode('simulation');worker=Mock();worker.poll.return_value=None;self.c.jobs['strategy']=worker
        with patch('subprocess.Popen') as launch:self.c.switch_mode('live');launch.assert_not_called()
        self.assertEqual(self.c.mode(),'live');self.assertTrue(self.c.running('strategy'))
        worker.terminate.assert_not_called();worker.kill.assert_not_called()
        # Real pending orders still prevent the reverse switch, even alongside SIM workers.
        self.state('live/us.sqlite',env='REAL',orders={'real':{'status':'UNKNOWN'}})
        with self.assertRaisesRegex(ValueError,'unknown'):self.c.switch_mode('simulation')
        self.assertEqual(self.c.mode(),'live')
    def test_legacy_simulation_activity_does_not_block_new_real_connection_requests(self):
        self.state('option_transport.sqlite',orders={'a':{'status':'INTENT'}})
        with patch('subprocess.Popen') as launch:
            launch.return_value.pid=123;launch.return_value.poll.return_value=None
            self.c.start('account');self.c.live_action({'action':'prepare'})
            self.assertEqual(launch.call_count,2)
    def test_live_holding_or_active_worker_blocks_simulation(self):
        self.state('live/us.sqlite',env='REAL',positions={'US.A':{'qty':2}})
        with self.assertRaisesRegex(ValueError,'holdings'):self.c.switch_mode('simulation')
        self.state('live/us.sqlite',env='REAL')
        worker=Mock();worker.poll.return_value=None;self.c.jobs['live_runner']=worker
        with self.assertRaisesRegex(ValueError,'worker'):self.c.switch_mode('simulation')
    def test_armed_live_portfolio_must_be_explicitly_paused(self):
        path=self.root/'data/trading/live/controls.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'enabled':True,'paused':False}))
        with self.assertRaisesRegex(ValueError,'Pause or stop'):self.c.switch_mode('simulation')
        path.write_text(json.dumps({'enabled':True,'paused':True,'loss_latched':True}))
        original=path.read_bytes();self.c.switch_mode('simulation');self.assertEqual(path.read_bytes(),original)
    def test_hk_sim_reservation_does_not_block_live_and_cannot_be_released_by_switch(self):
        self.c.switch_mode('simulation');path=self.root/'data/trading/hk_reservations.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE holds (state TEXT)');db.execute("INSERT INTO holds VALUES('HELD')")
        self.c.switch_mode('live');self.assertEqual(self.c.mode(),'live')
        with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT state FROM holds').fetchone()[0],'HELD')
    def test_unknown_simulated_hk_claim_allows_live_and_is_preserved(self):
        self.c.switch_mode('simulation');path=self.root/'data/trading/hk_submissions.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE submissions (order_id TEXT,claimed_at TEXT)')
            db.execute("INSERT INTO submissions VALUES(NULL,'2026-10-31T00:00:00+00:00')")
        self.c.switch_mode('live')
        with sqlite3.connect(path) as db:self.assertIsNone(db.execute('SELECT order_id FROM submissions').fetchone()[0])
    def test_corrupt_mode_and_unreadable_state_fail_closed(self):
        (self.data/'workspace_mode.json').write_text('{')
        with patch('subprocess.Popen') as launch:
            with self.assertRaises(ValueError):self.c.start('account')
            launch.assert_not_called()
        (self.data/'workspace_mode.json').unlink()
        self.c.switch_mode('simulation');path=self.root/'data/trading/paper.sqlite';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'invalid')
        self.c.switch_mode('live')
        live_path=self.root/'data/trading/live/us.sqlite';live_path.parent.mkdir(parents=True,exist_ok=True);live_path.write_bytes(b'invalid')
        with self.assertRaises(ValueError):self.c.switch_mode('simulation')
    def test_live_status_omits_simulation_budget_and_signals(self):
        self.state('paper.sqlite',initial=500,positions={'US.SIM':{'qty':1}})
        status=self.c.status()
        for key in ('simulation_total_budget_usd','budget_usd','scan','candidates','paper_bound','probe'):
            self.assertNotIn(key,status)
        self.assertEqual(status['workspace']['mode'],'live');self.assertEqual(status['positions'],[])
    def test_live_mode_supervisor_cannot_launch_simulation(self):
        stop=Mock();stop.is_set.side_effect=[False,True]
        with patch.object(self.c,'start') as start:self.c.supervise_automation(stop)
        start.assert_not_called()

    def test_display_quotes_use_only_selected_mode_symbols(self):
        import sys
        from trading.position_quotes import supervise
        self.state('paper.sqlite',positions={'US.SIM':{'qty':1}})
        self.state('live/us.sqlite',env='REAL',positions={'US.LIVE':{'qty':2}})
        (self.data/'account.json').write_text(json.dumps({'accounts':[{'positions':[{'code':'HK.00700','qty':3}]}]}))
        (self.root/'data/trading/options.report.json').write_text(json.dumps({'position':{'code':'US.OPTION'}}))
        for mode,expected in [('live',['HK.00700','US.LIVE']),('simulation',['US.OPTION','US.SIM'])]:
            sdk=Mock();sdk.RET_OK=0;context=sdk.OpenQuoteContext.return_value
            frame=Mock();frame.to_dict.return_value=[];context.get_market_snapshot.return_value=(0,frame)
            stop=Mock();stop.is_set.side_effect=[False,True]
            with patch.dict(sys.modules,{'moomoo':sdk}):supervise(stop,self.root,mode=lambda:mode)
            context.get_market_snapshot.assert_called_once_with(expected)
            context.close.assert_called_once()
    def test_http_mode_switch_excludes_other_account_paths_without_enabling_live(self):
        import threading,urllib.request,urllib.error
        from trading.desktop_server import Server,Handler
        server=Server(('127.0.0.1',0),Handler);server.token='fixture';server.controller=self.c
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        def post(path,payload):
            request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,
                data=json.dumps(payload).encode(),headers={'X-Local-Token':'fixture','Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=2) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as response:return response.code,json.load(response)
        try:
            with patch('subprocess.Popen') as launch:
                self.assertEqual(post('/workspace',{'mode':'simulation'})[0],200)
                self.assertEqual(post('/action',{'action':'account'})[0],400)
                self.assertEqual(post('/live',{'action':'prepare'})[0],400)
                self.assertEqual(post('/workspace',{'mode':'live'})[0],200)
                self.assertEqual(post('/action',{'action':'strategy'})[0],400)
                self.assertEqual(post('/workspace',{'mode':'live','enable':True})[0],400)
                launch.assert_not_called()
            self.assertFalse((self.root/'data/trading/live/controls.json').exists())
        finally:server.shutdown();server.server_close();worker.join(timeout=2)
