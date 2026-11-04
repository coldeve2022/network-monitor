import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from core.collector import Collector
from tests.qa_paths import isolated_paths

def point(ts,avg):return dict(ts=ts,type='ping',host='example',name='example',kind='base',avg=avg,loss=0,jitter=0,std=0)

class MergeTests(unittest.TestCase):
    def test_legacy_traffic_is_not_dropped_when_combined_with_new_duration(self):
        from core.statistics import accumulate,summarize
        c=self.c
        c.db.flush_minute(3600,{'traffic':{'all':{'rx_bps':100,'tx_bps':0,'rx_peak':100,'tx_peak':0}}})
        stats=c.db.load_minute_stats(3600)[('traffic','all')]
        accumulate(stats,{'type':'traffic','rx_bps':300,'tx_bps':0,'duration':1,'rx_bytes':300,'tx_bytes':0})
        self.assertEqual(summarize(stats,'traffic')['rx_bps'],200)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.context=isolated_paths(Path(self.temp.name));self.context.__enter__();self.c=Collector();self.c._base=[dict(host='example',name='example',kind='base')];self.c._rebuild_targets()
    def tearDown(self):self.c.close();self.context.__exit__(None,None,None);self.temp.cleanup()
    def test_hour_merges_counts_and_variance(self):
        for j in range(60):self.c.on_result(point(3600+j,10))
        self.c.on_result(point(3660,110));self.c._flush_minute(3660);self.c.db.aggregate_hour(3600)
        avg,std=self.c.db.conn.execute('SELECT avg,std FROM ping_hour WHERE ts=3600').fetchone()
        self.assertAlmostEqual(avg,710/61)
        self.assertAlmostEqual(std,12.698306053139,places=8)
        with patch('core.collector.time.time',return_value=3700):summary=self.c.compute_summary()
        self.assertEqual(summary['latency_avg'],11.6)
    def test_committed_minute_survives_crash_restart_and_cleanup(self):
        c=self.c;c.on_result(point(3600,10));c.on_result(point(3660,20));c.db.close()
        self.c=Collector()
        self.c.on_result(point(7260,30));self.c.on_result(point(7320,30))
        self.c.db.cleanup(100800)
        self.assertEqual(self.c.db.conn.execute('SELECT avg FROM ping_hour WHERE ts=3600').fetchone(),(10.0,))
