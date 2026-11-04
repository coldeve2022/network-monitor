import tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from tests.qa_paths import isolated_paths
from core.collector import Collector
import config,web.server as server

class ApiMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.ctx=isolated_paths(Path(self.tmp.name));self.ctx.__enter__()
        self.c=Collector();app=server.create_app(self.c)
        self.client=TestClient(app,base_url='http://127.0.0.1',headers={'X-NM-Token':getattr(app.state,'nm_token','')},raise_server_exceptions=False)
    def tearDown(self):self.client.close();self.c.close();self.ctx.__exit__(None,None,None);self.tmp.cleanup()
    def test_missing_key_field_does_not_clear_credentials(self):
        server.AI_KEY_FILE.write_text('{"key":"dummy"}')
        self.assertEqual(self.client.post('/api/ai-key',json={}).status_code,422)
        self.assertTrue(server.AI_KEY_FILE.exists())
    def test_extreme_integer_is_client_error(self):
        self.assertEqual(self.client.post('/api/config',json={'ping_interval':10**400}).status_code,422)
    def test_matching_untrusted_host_and_origin_rejected(self):
        self.assertIn(self.client.get('/api/overview',headers={'Host':'evil.test:8768','Origin':'http://evil.test:8768'}).status_code,(400,403))
    def test_invalid_session_token_cannot_change_settings(self):
        before=config.snapshot()
        self.assertEqual(self.client.post('/api/config',json={'ping_interval':9},headers={'X-NM-Token':'bad'}).status_code,403)
        self.assertEqual(before,config.snapshot())
