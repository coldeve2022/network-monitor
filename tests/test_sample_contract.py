import tempfile
import unittest
from pathlib import Path
from core.collector import Collector
from tests.qa_paths import isolated_paths


class SampleContractTests(unittest.TestCase):
    def test_unscheduled_sample_does_not_count_as_packet_loss(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, isolated_paths(Path(tmp)):
            c=Collector()
            try:
                target=c.ping_targets()[0]
                for ts,value,state in ((1000,10,'ok'),(1001,None,'skipped')):
                    c.on_result(dict(ts=ts,type='ping',avg=value,loss=100 if value is None else 0,jitter=0,std=0,state=state,**target))
                c._flush_minute(960)
                row=c.db.conn.execute('SELECT loss FROM ping_minute').fetchone()
                self.assertEqual(row[0],0,'unsent probe polluted packet-loss denominator')
            finally:c.close()

    def test_snapshot_has_session_sequence_and_explicit_state(self):
        import time
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, isolated_paths(Path(tmp)):
            c=Collector()
            try:
                target=c.ping_targets()[0]
                c.on_result(dict(ts=time.time(),type='ping',avg=10,loss=0,jitter=0,std=0,**target))
                ov=c.overview(); sample=ov['latest_by_target']['ping'][target['host']]
                self.assertEqual(sample.get('state'),'ok')
                self.assertTrue(ov.get('session_id'))
                self.assertGreater(sample.get('sequence',0),0)
            finally:c.close()
