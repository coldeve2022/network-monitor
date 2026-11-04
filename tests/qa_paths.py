"""All test resource paths and runtime mutations are restored, including failures."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
import config
import web.server as server


@contextmanager
def isolated_paths(root: Path):
    original = config.snapshot()
    with ExitStack() as stack:
        for name, filename in [('DB_PATH', 'metrics.db'), ('TARGETS_FILE', 'targets.json'), ('SETTINGS_FILE', 'settings.json'),('PROBES_FILE','probes.json')]:
            stack.enter_context(patch.object(config, name, root / filename))
        for name in ('TCP_TARGETS','DNS_SERVERS','DNS_QUERY_HOST','TRACE_TARGETS','TRAFFIC_INTERFACE','PROBE_REVISION'):
            stack.enter_context(patch.object(config,name,deepcopy(getattr(config,name))))
        stack.enter_context(patch.object(server, 'AI_KEY_FILE', root / 'ai_key.json'))
        try:
            yield
        finally:
            with config._RUNTIME_LOCK:
                config._RUNTIME.clear()
                config._RUNTIME.update(original)
