import tempfile,time,unittest
from pathlib import Path
from core.collector import Collector
from tests.qa_paths import isolated_paths

class ReplayTests(unittest.TestCase):
    def test_all_targets_are_replayable_after_small_shared_buffer_overflows(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:
                now=time.time()
                for step in range(100):
                    rows=[dict(ts=now-100+step,type='ping',host=f'192.0.2.{h}',name=str(h),kind='base',avg=10,loss=0,jitter=0,std=0) for h in range(1,34)]
                    c.on_result(dict(ts=now-100+step,type='ping_round',targets=rows))
                replay=c.replay(c.session_id,0,5000)
                self.assertEqual(len(replay['rows']),3300)
                sequence=replay['rows'][0]['sequence']
                self.assertEqual(len(c.replay(c.session_id,sequence,5000)['rows']),3299)
                self.assertFalse(replay['reset'])
            finally:c.close()
    def test_restart_returns_explicit_session_reset(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:self.assertTrue(c.replay('previous-session',999,100)['reset'])
            finally:c.close()
