import tempfile,time,unittest
from pathlib import Path
from core.collector import Collector
from tests.qa_paths import isolated_paths

class RawRecoveryTests(unittest.TestCase):
    def test_raw_after_legacy_base_is_not_omitted_on_crash(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector();ts=time.time();minute=int(ts//60)*60;target=c.ping_targets()[0]
            c.db.flush_minute(minute,{'ping':{target['host']:{'avg':10,'min':10,'max':10,'loss':0,'jitter':0,'std':0}}});c.close()
            c=Collector();c.on_result(dict(ts=ts,type='ping',avg=30,loss=0,jitter=0,std=0,**target));c.db.close()
            restarted=Collector()
            try:
                restarted.on_result(dict(ts=ts+.001,type='ping',avg=90,loss=0,jitter=0,std=0,**target));restarted._flush_minute(minute)
                self.assertAlmostEqual(restarted.db.conn.execute('SELECT avg FROM ping_minute').fetchone()[0],130/3)
            finally:restarted.close()
    def test_committed_raw_current_minute_is_recovered_once_after_crash(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector();ts=time.time();target=c.ping_targets()[0]
            c.on_result(dict(ts=ts,type='ping',avg=10,loss=0,jitter=0,std=0,**target))
            c.db.close()  # Drain committed raw, deliberately omit Collector minute finalization.
            restarted=Collector()
            try:
                restarted.on_result(dict(ts=ts+.001,type='ping',avg=30,loss=0,jitter=0,std=0,**target))
                restarted._flush_minute(int(ts//60)*60)
                row=restarted.db.conn.execute('SELECT avg FROM ping_minute').fetchone()
                self.assertEqual(row,(20.0,))
            finally:restarted.close()
