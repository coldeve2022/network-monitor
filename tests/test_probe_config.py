import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import config

class ProbeConfigTests(unittest.TestCase):
    def test_probe_targets_persist_and_invalid_batch_is_atomic(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,patch.object(config,'PROBES_FILE',Path(tmp)/'probes.json',create=True):
            original=(config.TCP_TARGETS,config.DNS_SERVERS,config.DNS_QUERY_HOST,config.TRACE_TARGETS)
            try:
                config.set_probe_config({'tcp_targets':[{'name':'service','host':'example.test','port':443}],'dns_servers':['192.0.2.53'],'dns_query_host':'example.test','trace_targets':['192.0.2.1'],'traffic_interface':''})
                before=config.PROBES_FILE.read_bytes()
                with self.assertRaises(ValueError):config.set_probe_config({'tcp_targets':[{'name':'bad','host':'example.test','port':99999}]})
                self.assertEqual(config.PROBES_FILE.read_bytes(),before)
                self.assertEqual(config.TCP_TARGETS[0]['port'],443)
            finally:config.TCP_TARGETS,config.DNS_SERVERS,config.DNS_QUERY_HOST,config.TRACE_TARGETS=original
