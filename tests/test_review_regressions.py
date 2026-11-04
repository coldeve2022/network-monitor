import concurrent.futures,json,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import config
from core.collector import Collector
from core.ping_probe import PingProbe
from core.probe_health import ProbeHealth
from core.trace_manager import TraceManager
from tests.qa_paths import isolated_paths

class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.ctx=isolated_paths(Path(self.tmp.name));self.ctx.__enter__();self.c=Collector()
    def tearDown(self):self.c.close();self.ctx.__exit__(None,None,None);self.tmp.cleanup()
    def point(self,ts=None,**values):
        return dict(ts=time.time() if ts is None else ts,type='ping',avg=10,loss=0,jitter=0,std=0,**values)
    def test_legacy_failed_minute_recovers_without_partial_statistics(self):
        target=self.c.ping_targets()[0];minute=int(time.time()//60)*60
        self.c.db.flush_minute(minute,{'ping':{target['host']:{'avg':None,'min':None,'max':None,'loss':100,'jitter':0,'std':0}}})
        self.c.close();self.c=Collector()
        self.c.on_result(self.point(minute+1,**target));self.c._flush_minute(minute)
        self.assertEqual(self.c.db.conn.execute('SELECT avg,min,max,loss FROM ping_minute').fetchone(),(10.0,10.0,10.0,50.0))
    def test_readded_ping_instance_has_a_fresh_window(self):
        target=dict(host='192.0.2.1',name='a',kind='base',hop=None,trace_of=None,target_id='OLD')
        rows=[];provider=[target];p=PingProbe(rows.append,lambda:provider)
        with patch('core.ping_probe.ping',return_value=None):p._round()
        provider[0]={**target,'target_id':'NEW'}
        with patch('core.ping_probe.ping',return_value=10):p._round()
        self.assertEqual(rows[-1]['targets'][0]['loss'],0)
    def test_retired_trace_hop_cannot_publish_after_topology_change(self):
        self.c.update_trace('a',['192.0.2.1']);old=next(t for t in self.c.ping_targets() if t['kind']=='hop')
        self.c.update_trace('a',['192.0.2.2']);self.c.on_result(self.point(**old))
        self.assertNotIn('192.0.2.1',self.c.overview()['latest_by_target'].get('ping',{}))
    def test_old_tcp_config_result_cannot_replace_current_endpoint(self):
        config.set_probe_config({'tcp_targets':[{'name':'service','host':'old.example','port':443}]})
        revision=getattr(config,'PROBE_REVISION',0)
        config.set_probe_config({'tcp_targets':[{'name':'service','host':'new.example','port':443}]})
        self.c.on_result(dict(ts=time.time(),type='tcp',name='service',host='old.example',port=443,ms=10,probe_revision=revision))
        self.assertNotIn('service',self.c.overview()['latest_by_target'].get('tcp',{}))
    def test_partial_measurement_is_not_complete_healthy(self):
        targets=self.c.ping_targets()[:2]
        rows=[self.point(**targets[0]),{**self.point(**targets[1]),'avg':None,'loss':None,'state':'skipped'}]
        self.c.on_result(dict(ts=time.time(),type='ping_round',targets=rows))
        ov=self.c.overview();self.assertNotEqual(ov['status'],'good');self.assertNotEqual(ov['probe_health']['ping']['status'],'running')
    def test_backward_clock_starts_new_epoch_and_accepts_new_values(self):
        target=self.c.ping_targets()[0]
        with patch('core.collector.time.time',return_value=1000):self.c.on_result(self.point(1000,**target))
        with patch('core.collector.time.time',return_value=900):
            row={**self.point(900,**target),'avg':20};self.c.on_result(row);ov=self.c.overview()
        self.assertEqual(ov['latest_by_target']['ping'][target['host']]['avg'],20)
        self.assertGreater(ov.get('time_epoch',0),0)
        health=ProbeHealth();health.record_sample('ping',1000)
        self.assertNotEqual(health.snapshot(900)['ping']['status'],'running')
    def test_slow_raw_storage_does_not_hold_acquisition_lock(self):
        entered=threading.Event();release=threading.Event();target=self.c.ping_targets()[0]
        original=self.c.db.save_raw_batch
        def blocked(rows):entered.set();release.wait(2);return original(rows)
        with patch.object(self.c.db,'save_raw_batch',side_effect=blocked),concurrent.futures.ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.c.on_result,self.point(**target));self.assertTrue(entered.wait(1))
            second=pool.submit(self.c.on_result,self.point(time.time()+.01,**target))
            try:self.assertIsNone(second.result(.3))
            finally:release.set();first.result(3);second.result(3)
    def test_pre_clock_change_inflight_result_cannot_block_new_epoch(self):
        target=self.c.ping_targets()[0]
        with patch('core.collector.time.time',return_value=1000):self.c.on_result(self.point(1000,**target))
        with patch('core.collector.time.time',return_value=900):
            self.c.on_result({**self.point(1001,**target),'started_at':1000,'avg':11})
            self.c.on_result({**self.point(900,**target),'avg':20})
            self.assertEqual(self.c.overview()['latest_by_target']['ping'][target['host']]['avg'],20)
    def test_slow_target_file_save_does_not_hold_acquisition_lock(self):
        entered=threading.Event();release=threading.Event();target=self.c.ping_targets()[0]
        original=self.c._save_custom_targets
        def blocked(*args):entered.set();release.wait(2);return original(*args)
        with patch.object(self.c,'_save_custom_targets',side_effect=blocked),concurrent.futures.ThreadPoolExecutor(2) as pool:
            update=pool.submit(self.c.add_target,'new','new.example');self.assertTrue(entered.wait(1))
            sample=pool.submit(self.c.on_result,self.point(**target))
            try:self.assertIsNone(sample.result(.3))
            finally:release.set();update.result(3);sample.result(3)
    def test_trace_resolution_does_not_hold_shutdown(self):
        entered=threading.Event();release=threading.Event()
        def blocked(host):entered.set();release.wait(3);return '192.0.2.1'
        manager=TraceManager(self.c)
        getter=config.get
        with patch.object(config,'TRACE_TARGETS',['trace.example']),patch('core.trace_manager.config.get',side_effect=lambda k:True if k=='enable_traceroute' else getter(k)),patch('core.traceroute.socket.gethostbyname',side_effect=blocked):
            manager.start()
            try:
                self.assertTrue(entered.wait(1));manager.stop();manager.join(.3);self.assertFalse(manager.is_alive())
            finally:release.set();manager.stop();manager.join(2)
    def test_removed_trace_configuration_removes_old_hops(self):
        self.c.update_trace('removed.example',['192.0.2.1'])
        with patch.object(config,'TRACE_TARGETS',[]):TraceManager(self.c)._trace_all()
        self.assertNotIn('removed.example',self.c.traces)

class LegacyRecovery(unittest.TestCase):
    def test_legacy_tcp_only_and_incomplete_existing_hour_are_recovered(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            from storage.database import Database
            path=config.DB_PATH;db=Database(path);hour=int(time.time()//3600)*3600-3600
            for ts,value in ((hour,10),(hour+60,110)):
                db.flush_minute(ts,{'ping':{'a':{'avg':value,'min':value,'max':value,'loss':0,'jitter':0,'std':0}},'tcp':{'only-tcp':{'ms':25}}})
            db.conn.execute('INSERT OR REPLACE INTO ping_hour VALUES (?,?,?,?,?,?,?,?)',(hour,'a',10,10,10,0,0,0));db.conn.execute('DELETE FROM dirty_hours');db.conn.commit();db.close()
            db=Database(path)
            try:
                db.cleanup(time.time()+86400)
                self.assertEqual(db.conn.execute('SELECT avg FROM ping_hour WHERE target="a"').fetchone(),(60.0,))
                self.assertEqual(db.conn.execute('SELECT ms FROM tcp_hour WHERE name="only-tcp"').fetchone(),(25.0,))
            finally:db.close()
