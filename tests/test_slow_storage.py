import concurrent.futures,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests.qa_paths import isolated_paths
from core.collector import Collector
from web.server import create_app

class SlowStorageTests(unittest.TestCase):
    def test_slow_history_does_not_block_websocket(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,isolated_paths(Path(tmp)):
            c=Collector();app=create_app(c);entered=threading.Event();release=threading.Event()
            def slow(*args):entered.set();release.wait(2);return []
            try:
                with TestClient(app,base_url='http://127.0.0.1') as client,concurrent.futures.ThreadPoolExecutor(2) as pool:
                    with client.websocket_connect('ws://127.0.0.1/ws?token='+app.state.nm_token) as ws,patch.object(c.db,'query_minute',side_effect=slow):
                        query=pool.submit(client.get,'/api/history')
                        try:
                            self.assertTrue(entered.wait(1))
                            receipt=pool.submit(ws.receive_json)
                            c.broadcast({'type':'test_heartbeat'})
                            self.assertEqual(receipt.result(.5)['type'],'test_heartbeat')
                        finally:release.set();query.result(3)
            finally:c.close()
