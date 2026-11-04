import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import web.server as server
import test_run


class FixtureIsolationTests(unittest.TestCase):
    def test_smoke_preserves_existing_target_settings_and_key(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            targets = root / 'targets.json'
            settings = root / 'settings.json'
            key = root / 'ai_key.json'
            targets.write_text('{"targets":[{"name":"existing","host":"114.114.114.114"}]}')
            settings.write_text('{"ping_interval":7}')
            key.write_text('{"key":"dummy"}')
            before = [p.read_bytes() for p in (targets, settings, key)]
            runtime = config.snapshot()
            with patch.object(config, 'TARGETS_FILE', targets), patch.object(config, 'SETTINGS_FILE', settings), patch.object(server, 'AI_KEY_FILE', key):
                result = test_run.run_smoke()
            self.assertGreater(result['samples'], 0)
            self.assertEqual(before, [p.read_bytes() for p in (targets, settings, key)])
            self.assertEqual(runtime, config.snapshot())

    def test_failure_restores_paths_and_runtime(self):
        paths = (config.DB_PATH, config.TARGETS_FILE, config.SETTINGS_FILE, server.AI_KEY_FILE)
        runtime = config.snapshot()
        with patch.object(test_run, '_smoke_checks', side_effect=RuntimeError('controlled failure'), create=True):
            with self.assertRaisesRegex(RuntimeError, 'controlled failure'):
                test_run.run_smoke()
        self.assertEqual(paths, (config.DB_PATH, config.TARGETS_FILE, config.SETTINGS_FILE, server.AI_KEY_FILE))
        self.assertEqual(runtime, config.snapshot())
