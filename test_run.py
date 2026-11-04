"""Offline, isolated smoke checks. No user data, network probes or paid AI calls."""
import json
import tempfile
import time
from pathlib import Path
from tests.qa_paths import isolated_paths
from core.collector import Collector
import config


def _smoke_checks(collector):
    now = time.time()
    samples = 0
    for ts in (now - 70, now):
        for target in collector.ping_targets():
            collector.on_result(dict(ts=ts, type='ping', avg=12, min=12, max=12,
                std=0, loss=0, jitter=0, **target))
            samples += 1
        collector.on_result(dict(ts=ts, type='tcp', name='fixture', host='example.invalid', port=443, ms=20))
        collector.on_result(dict(ts=ts, type='dns', server='192.0.2.1', ms=15))
        collector.on_result(dict(ts=ts, type='traffic', rx_bps=100, tx_bps=50))
    config.set_many({'ping_interval': 2})
    assert collector.db.query_minute('ping', 1), 'completed minute must be saved'
    assert collector.overview()['latest_by_target']['tcp']['fixture']['ms'] == 20
    return {'samples': samples, 'history_rows': len(collector.db.query_minute('ping', 1))}


def run_smoke():
    with tempfile.TemporaryDirectory(prefix='smoke-', dir=Path(__file__).parent / 'tests') as tmp:
        with isolated_paths(Path(tmp)):
            collector = Collector()
            try:
                return _smoke_checks(collector)
            finally:
                collector.close()


if __name__ == '__main__':
    print(json.dumps(run_smoke(), ensure_ascii=False))
