"""Isolated browser QA fixture; never opens the user's data directory."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
import config
from core.collector import Collector
from web.server import create_app
from tests.qa_paths import isolated_paths

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, isolated_paths(Path(directory)):
        collector = Collector()
        collector.set_gateway('192.168.1.1')
        now = time.time()
        for step in range(30):
            ts = now - (30 - step) * 60
            for i, target in enumerate(collector.ping_targets()):
                avg = None if step % 7 == 0 and i else 10 + i * 15 + step % 6
                collector.on_result(dict(ts=ts, type='ping', avg=avg, min=avg, max=avg,
                                         std=3, jitter=2, loss=100 if avg is None else 0, **target))
        for i, target in enumerate(collector.ping_targets()):
            avg = 10 + i * 15
            collector.on_result(dict(ts=now, type='ping', avg=avg, min=avg, max=avg,
                                     std=3, jitter=2, loss=0, **target))
        app = create_app(collector)
        try:
            uvicorn.run(app, host='127.0.0.1', port=8879, log_level='warning')
        finally:
            collector.close()
