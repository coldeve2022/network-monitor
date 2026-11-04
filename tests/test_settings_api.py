import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
from core.collector import Collector
from web.server import create_app, _collect_analyze_summary
import web.server as server


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / 'settings.json'
        self.original = config.snapshot()
        self.patcher = patch.object(config, 'SETTINGS_FILE', self.path, create=True)
        self.patcher.start()

    def tearDown(self):
        config._RUNTIME.clear()
        config._RUNTIME.update(self.original)
        self.patcher.stop()
        self.temp.cleanup()

    def test_settings_are_persisted(self):
        config.set_many({'ping_interval': 2.5, 'enable_traceroute': True})
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['ping_interval'], 2.5)
        self.assertIs(saved['enable_traceroute'], True)

    def test_invalid_batch_is_atomic(self):
        with self.assertRaises(ValueError):
            config.set_many({'ping_interval': 3, 'dns_interval': 'nan'})
        self.assertEqual(config.get('ping_interval'), self.original['ping_interval'])

    def test_invalid_bool_is_rejected(self):
        with self.assertRaises(ValueError):
            config.set_many({'enable_traceroute': 'typo'})

    def test_storage_failure_keeps_runtime(self):
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                config.set_many({'ping_interval': 9})
        self.assertEqual(config.snapshot(), self.original)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        root = Path(self.temp.name)
        self.original_settings = config.snapshot()
        self.patches = [patch.object(config, 'DB_PATH', root / 'metrics.db'),
                        patch.object(config, 'TARGETS_FILE', root / 'targets.json'),
                        patch.object(config, 'SETTINGS_FILE', root / 'settings.json', create=True),
                        patch.object(server, 'AI_KEY_FILE', root / 'ai_key.json')]
        for p in self.patches:
            p.start()
        self.collector = Collector()
        app=create_app(self.collector)
        self.client = TestClient(app,base_url='http://127.0.0.1',headers={'X-NM-Token':app.state.nm_token})

    def tearDown(self):
        self.client.close()
        self.collector.close()
        with config._RUNTIME_LOCK:
            config._RUNTIME.clear()
            config._RUNTIME.update(self.original_settings)
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def test_invalid_metric_has_client_error(self):
        self.assertEqual(self.client.get('/api/history?metric=nope').status_code, 422)

    def test_invalid_history_window_has_client_error(self):
        for hours in ('-1', '0', '999999'):
            self.assertEqual(self.client.get('/api/history?hours=' + hours).status_code, 422)

    def test_events_limit_is_bounded(self):
        self.assertEqual(self.client.get('/api/events?limit=-1').status_code, 422)

    def test_invalid_setting_returns_client_error(self):
        self.assertEqual(self.client.post('/api/config', json={'ping_interval': 'bad'}).status_code, 422)

    def test_non_string_target_returns_client_error(self):
        self.assertEqual(self.client.post('/api/targets/add', json={'host': 42}).status_code, 422)

    def test_invalid_analysis_hours_returns_client_error(self):
        self.assertEqual(self.client.post('/api/analyze', json={'hours': 'bad'}).status_code, 422)

    def test_bad_host_is_rejected_before_probing(self):
        for host in ('https://example.com', 'bad host', '999.1.1.1', '--flag'):
            self.assertEqual(self.client.post('/api/targets/add', json={'host': host}).status_code, 422)

    def test_local_diagnostics_without_ai_key(self):
        response = self.client.get('/api/diagnostics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['level'], 'unknown')

    def test_csv_export_contains_header(self):
        response = self.client.get('/api/export?metric=ping&hours=24')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.headers['content-type'])
        self.assertIn('target,avg', response.text)

    def test_analysis_keeps_offline_targets(self):
        import time
        host = self.collector.targets[0]['host']
        self.collector.db.flush_minute(int(time.time()) // 60 * 60 - 60,
            {'ping': {host: {'avg': None, 'min': None, 'max': None, 'loss': 100, 'jitter': 0, 'std': 0}}})
        targets = _collect_analyze_summary(self.collector, 1)['targets']
        self.assertTrue(any(t['host'] == host and t['loss_pct'] == 100 for t in targets))

    def test_analysis_includes_unflushed_live_data(self):
        import time
        target = self.collector.targets[0]
        self.collector.on_result(dict(ts=time.time(), type='ping', avg=12, min=12, max=12, std=0, loss=0, jitter=0, **target))
        targets = _collect_analyze_summary(self.collector, 1)['targets']
        self.assertTrue(any(t['host'] == target['host'] and t['avg_ms'] == 12 for t in targets))

    def test_foreign_origin_cannot_change_settings(self):
        response = self.client.post('/api/config', json={}, headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)

    def test_lifespan_does_not_leave_broadcaster(self):
        app=create_app(self.collector)
        with TestClient(app,base_url='http://127.0.0.1',headers={'X-NM-Token':app.state.nm_token}) as client:
            self.assertEqual(client.get('/api/overview').status_code, 200)


if __name__ == '__main__':
    unittest.main()

