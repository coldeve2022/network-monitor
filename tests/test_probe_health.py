import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from core.tcp_probe import TcpProbe
from core.collector import Collector
from tests.qa_paths import isolated_paths

class ProbeHealthTests(unittest.TestCase):
    def test_transient_callback_error_does_not_silently_kill_probe(self):
        recovered=threading.Event();calls=[]
        def publish(row):
            calls.append(row)
            if len(calls)==1:raise OSError('temporary storage failure')
            recovered.set();probe.stop()
        probe=TcpProbe(publish)
        with patch('core.tcp_probe.socket.create_connection',return_value=Mock(__enter__=Mock(),__exit__=Mock())),patch('core.tcp_probe.wait_interval',return_value=False):
            probe.start()
            try:self.assertTrue(recovered.wait(1),'probe never recovered after callback error')
            finally:probe.stop();probe.join(1)

    def test_stale_measurements_cannot_retain_healthy_overview(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:
                target=c.ping_targets()[0]
                c.on_result(dict(ts=1000,type='ping',avg=10,loss=0,jitter=0,std=0,**target));c._last_status='good'
                with patch('core.collector.time.time',return_value=2000):ov=c.overview()
                self.assertEqual(ov['status'],'unknown')
                self.assertEqual(ov['probe_health']['ping']['status'],'stale')
            finally:c.close()
