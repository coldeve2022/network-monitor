import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from core.collector import Collector
from tests.qa_paths import isolated_paths
import config,main

class TargetLifecycleTests(unittest.TestCase):
    def test_corrupt_targets_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            config.TARGETS_FILE.write_bytes(b'{broken user data')
            c=Collector()
            try:
                self.assertEqual(config.TARGETS_FILE.read_bytes(),b'{broken user data')
                self.assertTrue(c.overview().get('configuration_error'))
            finally:c.close()
    def test_delete_readd_has_new_identity_and_no_old_latest(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector()
            try:
                c.add_target('temporary','example.test');first=next(t for t in c.ping_targets() if t['host']=='example.test')
                c.on_result(dict(ts=time.time(),type='ping',avg=10,loss=0,jitter=0,std=0,**first))
                c.remove_target('example.test');c.add_target('again','example.test')
                second=next(t for t in c.ping_targets() if t['host']=='example.test')
                self.assertNotEqual(first.get('target_id'),second.get('target_id'))
                self.assertNotIn('example.test',c.overview()['latest_by_target'].get('ping',{}))
                c.on_result(dict(ts=time.time()+.1,type='ping',avg=99,loss=0,jitter=0,std=0,**first))
                self.assertNotIn('example.test',c.overview()['latest_by_target'].get('ping',{}))
            finally:c.close()
    def test_gateway_uses_lowest_active_route_metric(self):
        text='0.0.0.0 0.0.0.0 192.168.1.1 192.168.1.2 100\n0.0.0.0 0.0.0.0 10.0.0.1 10.0.0.2 1'
        with patch.object(main.subprocess,'check_output',return_value=text):self.assertEqual(main.detect_gateway(),'10.0.0.1')
