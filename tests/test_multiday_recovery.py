import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from core.collector import Collector
from tests.qa_paths import isolated_paths

class MultidayTests(unittest.TestCase):
    def test_seventy_two_hour_clock_advance_preserves_all_committed_observations(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector();base=int(time.time()//3600)*3600-71*3600
            try:
                for hour in range(72):
                    for offset in (0,60):
                        ts=base+hour*3600+offset
                        with patch('core.collector.time.time',return_value=ts),patch('core.collector.time.monotonic',return_value=ts):
                            c.on_result(dict(ts=ts,type='ping',host='example',name='example',kind='base',avg=20,loss=0,jitter=0,std=0))
                c._flush_minute(c._minute_key);c.db.aggregate_hour(c._minute_key//3600*3600)
                with patch('core.collector.time.time',return_value=base+71*3600+70):
                    stats=c.db.query_stats('ping',168,'example')
                    self.assertEqual(stats['count'],144)
                    self.assertEqual(stats['avg']/stats['ok'],20)
                    self.assertLessEqual(c.db.conn.execute('SELECT COUNT(*) FROM ping_minute').fetchone()[0],50)
            finally:c.close()
