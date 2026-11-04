import tempfile
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from core.collector import Collector
from core.evaluator import Evaluator
from storage.database import Database


def point(ts, avg=10, host='example', kind='base', loss=0):
    return dict(ts=ts, type='ping', name=host, host=host, kind=kind,
                avg=avg, min=avg, max=avg, std=0, jitter=0, loss=loss)


class DataCorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        root = Path(self.temp.name)
        self.patches = [patch.object(config, 'DB_PATH', root / 'metrics.db'),
                        patch.object(config, 'TARGETS_FILE', root / 'targets.json')]
        for p in self.patches:
            p.start()
        self.collector = Collector()
        self.collector._base = [dict(host='example', name='example', kind='base')]
        self.collector._rebuild_targets()

    def tearDown(self):
        try:
            self.collector.close()
        except Exception:
            pass
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def test_minute_extrema_and_std_use_current_rtts_not_rolling_fields(self):
        c = self.collector
        for ts, avg in ((60, 10), (61, 30)):
            sample = point(ts, avg)
            sample.update(min=1, max=999, std=888)
            c.on_result(sample)
        c._flush_minute(60)
        self.assertEqual(c.db.conn.execute('SELECT min, max, std FROM ping_minute').fetchone(), (10, 30, 10))

    def test_live_summary_std_uses_current_rtts(self):
        samples = [point(990, 10), point(991, 30), point(992, None)]
        for sample in samples:
            sample['std'] = 888
        with patch('core.collector.time.time', return_value=1000):
            summary = self.collector.compute_target_summaries(buffer=samples, targets=self.collector.targets)['example']
        self.assertEqual(summary['std_avg'], 10)
        self.assertEqual(summary['cv'], 50)

    def test_restart_in_same_minute_resumes_exact_sample_counts(self):
        ts = int(time.time() // 60) * 60
        c = self.collector
        c.on_result(point(ts, 10))
        c.on_result(point(ts + 1, 20))
        c.on_result(point(ts + 2, None))
        for value in (10, 20):
            c.on_result(dict(ts=ts, type='tcp', name='endpoint', ms=value))
            c.on_result(dict(ts=ts, type='dns', server='resolver', ms=value))
            c.on_result(dict(ts=ts, type='traffic', rx_bps=value, tx_bps=value * 2))
        c.close()
        self.collector = restarted = Collector()
        restarted.on_result(point(ts + 3, 30))
        restarted.on_result(dict(ts=ts + 3, type='tcp', name='endpoint', ms=30))
        restarted.on_result(dict(ts=ts + 3, type='dns', server='resolver', ms=30))
        restarted.on_result(dict(ts=ts + 3, type='traffic', rx_bps=30, tx_bps=60))
        restarted._flush_minute(ts)
        self.assertEqual(restarted.db.conn.execute('SELECT avg, loss FROM ping_minute').fetchone(), (20, 25))
        self.assertAlmostEqual(restarted.db.conn.execute('SELECT std FROM ping_minute').fetchone()[0], (200 / 3) ** .5)
        self.assertEqual(restarted.db.conn.execute('SELECT ms FROM tcp_minute').fetchone(), (20,))
        self.assertEqual(restarted.db.conn.execute('SELECT ms FROM dns_minute').fetchone(), (20,))
        self.assertEqual(restarted.db.conn.execute('SELECT rx_bps, tx_bps, rx_peak FROM traffic_minute').fetchone(), (20, 40, 30))

    def test_legacy_minute_is_preserved_as_one_approximate_aggregate(self):
        ts = int(time.time() // 60) * 60
        c = self.collector
        c.db.flush_minute(ts, {'ping': {'example': {
            'avg': 10, 'min': 5, 'max': 15, 'loss': 50, 'jitter': 2, 'std': 3}}})
        c.close()
        self.collector = c = Collector()
        c.on_result(point(ts + 1, 30))
        c._flush_minute(ts)
        self.assertEqual(c.db.conn.execute('SELECT avg, loss, min, max FROM ping_minute').fetchone(), (20, 25, 5, 30))

    def test_explicit_aggregate_replacement_invalidates_previous_counts(self):
        c = self.collector
        c.on_result(point(60, 10))
        c._flush_minute(60)
        c.db.flush_minute(60, {'ping': {'example': {
            'avg': 50, 'min': 50, 'max': 50, 'loss': 0, 'jitter': 0, 'std': 0}}})
        stats = c.db.load_minute_stats(60)[('ping', 'example')]
        self.assertEqual(stats['avg'], 50)
        self.assertTrue(stats['legacy_approximate'])

    def test_minute_and_stats_save_roll_back_together(self):
        c = self.collector
        c.db.conn.execute("CREATE TRIGGER fail_stats BEFORE INSERT ON minute_stats BEGIN SELECT RAISE(ABORT, 'disk error'); END")
        c.on_result(point(60, 10))
        with self.assertRaises(sqlite3.IntegrityError):
            c._flush_minute(60)
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM ping_minute').fetchone(), (0,))
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM minute_stats').fetchone(), (0,))
        c.db.conn.execute('DROP TRIGGER fail_stats')

    def test_minute_sufficient_stats_expire_after_complete_boundary_hour(self):
        c = self.collector
        c.on_result(point(60))
        c._flush_minute(60)
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM minute_stats').fetchone(), (1,))
        c.db.cleanup(60 + config.MINUTE_RETENTION_HOURS * 3600 + 1)
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM ping_minute').fetchone(), (0,))
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM minute_stats').fetchone(), (1,))
        c.db.cleanup(3600 + config.MINUTE_RETENTION_HOURS * 3600)
        self.assertEqual(c.db.conn.execute('SELECT COUNT(*) FROM minute_stats').fetchone(), (0,))

    def test_late_samples_preserve_previously_flushed_minute(self):
        c = self.collector
        c.on_result(point(60, 10))
        c.on_result(point(120, 30))
        c.on_result(point(61, 20))
        c.on_result(point(180, 40))
        rows = c.db.conn.execute('SELECT ts, avg FROM ping_minute ORDER BY ts').fetchall()
        self.assertEqual(rows, [(60, 15), (120, 30)])
        self.assertEqual(c._minute_key, 180)

    def test_late_sample_refreshes_completed_hour(self):
        c = self.collector
        with patch('core.collector.time.time', return_value=3720):
            c.on_result(point(3590, 10))
            c.on_result(point(3720, 30))
            c.on_result(point(3591, 20))
        self.assertEqual(c.db.conn.execute('SELECT avg FROM ping_hour').fetchone(), (15,))

    def test_failed_target_save_keeps_disk_and_runtime_unchanged(self):
        c = self.collector
        saved = config.TARGETS_FILE.read_bytes()
        before = list(c.targets)
        with patch.object(Path, 'replace', side_effect=OSError('disk full')):
            self.assertFalse(c.add_target('new', 'new.example')[0])
            self.assertFalse(c.remove_target('example')[0])
        self.assertEqual(c.targets, before)
        self.assertEqual(config.TARGETS_FILE.read_bytes(), saved)
        self.assertEqual(list(config.TARGETS_FILE.parent.glob('*.tmp')), [])

    def test_successful_target_mutation_survives_restart(self):
        c = self.collector
        self.assertTrue(c.add_target('new', 'new.example')[0])
        restarted = Collector()
        try:
            self.assertIn('new.example', [t['host'] for t in restarted.targets])
        finally:
            restarted.close()

    def test_overview_latest_by_target_preserves_all_targets_and_timestamp_order(self):
        c = self.collector
        c.on_result(point(100, 10))
        c.on_result(point(101, 20, host='second'))
        c.on_result(point(99, 99))
        c.on_result(dict(ts=102, type='tcp', name='tcp-one', ms=3))
        c.on_result(dict(ts=103, type='tcp', name='tcp-two', ms=4))
        c.on_result(dict(ts=104, type='dns', server='resolver', ms=5))
        latest = c.overview()['latest_by_target']
        self.assertEqual(latest['ping']['example']['avg'], 10)
        self.assertEqual(latest['ping']['second']['avg'], 20)
        self.assertEqual(latest['tcp']['tcp-one']['ms'], 3)
        self.assertEqual(latest['tcp']['tcp-two']['ms'], 4)
        self.assertEqual(latest['dns']['resolver']['ms'], 5)
        c.on_result(point(105, 30))
        self.assertEqual(latest['ping']['example']['avg'], 10)

    def test_partial_outage_affects_health(self):
        evaluator = Evaluator(lambda ev: None)
        self.assertEqual(evaluator.evaluate([point(0), point(0, None, 'other', loss=100)]), 'bad')

    def test_live_summary_counts_current_failed_packets(self):
        with patch('core.collector.time.time', return_value=1000):
            result = self.collector.compute_target_summaries(buffer=[point(990), point(991, None, loss=50)],
                                                           targets=self.collector.targets)['example']
        self.assertEqual(result['availability'], 50)
        self.assertEqual(result['loss_avg'], 50)

    def test_all_offline_target_has_summary(self):
        with patch('core.collector.time.time', return_value=1000):
            result = self.collector.compute_target_summaries(buffer=[point(990, None, loss=10)],
                                                           targets=self.collector.targets)
        self.assertEqual(result['example']['availability'], 0)
        self.assertEqual(result['example']['loss_avg'], 100)
        self.assertIsNone(result['example']['latency_avg'])

    def test_persisted_loss_uses_outcomes_not_rolling_windows(self):
        c = self.collector
        c.on_result(point(60, loss=99))
        c.on_result(point(61, None, loss=10))
        c._flush_minute(60)
        row = c.db.conn.execute('SELECT loss FROM ping_minute').fetchone()
        self.assertEqual(row[0], 50)

    def test_offline_minutes_affect_overview_summary(self):
        c = self.collector
        c.on_result(point(60))
        c.on_result(point(120, None, loss=100))
        c._flush_minute(120)
        with patch('storage.database.time.time', return_value=180):
            result = c.compute_summary()
        self.assertEqual(result['availability'], 50)
        self.assertEqual(result['loss_avg'], 50)

    def test_duplicate_hop_does_not_change_base_history_or_summary(self):
        c = self.collector
        items = [point(60, None, kind='hop', loss=100), point(60, 10)]
        c.on_result(dict(ts=60, type='ping_round', targets=items))
        c._flush_minute(60)
        row = c.db.conn.execute('SELECT avg, loss FROM ping_minute').fetchone()
        self.assertEqual(row, (10, 0))
        with patch('core.collector.time.time', return_value=65):
            self.assertEqual(c.compute_target_summaries()['example']['availability'], 100)

    def test_skipped_first_minute_of_hour_still_rolls_previous_hour(self):
        c = self.collector
        c.on_result(point(3590))
        with patch('core.collector.time.time', return_value=3720):
            c.on_result(point(3720))
        self.assertEqual(c.db.conn.execute('SELECT ts, avg FROM ping_hour').fetchall(), [(0, 10)])

    def test_hour_history_remains_queryable_after_minute_cleanup(self):
        c = self.collector
        now = 40 * 86400
        old = now - 48 * 3600
        c.on_result(point(old))
        c._flush_minute(old)
        c.db.aggregate_hour(old)
        c.db.cleanup(now)
        with patch('storage.database.time.time', return_value=now):
            rows = c.db.query_minute('ping', 72, 'example')
        self.assertEqual([r[:3] for r in rows], [(old, 'example', 10)])

    def test_remove_base_preserves_discovered_hop_positions(self):
        c = self.collector
        c.update_trace('route', ['example', None, 'other'])
        self.assertTrue(c.remove_target('example')[0])
        self.assertEqual(c.traces['route'], ['example', None, 'other'])
        self.assertEqual(c._base, [])
        self.assertFalse(c.remove_target('other')[0])

    def test_first_run_exposes_default_targets(self):
        config.TARGETS_FILE.unlink()
        first = Collector()
        try:
            self.assertTrue(first.ping_targets())
        finally:
            first.close()

    def test_long_history_keeps_minutes_without_hourly_rollup(self):
        now = 40 * 86400 + 1800
        ts = now - 24 * 3600 + 60
        c = self.collector
        c.on_result(point(ts))
        c._flush_minute(int(ts // 60) * 60)
        with patch('storage.database.time.time', return_value=now):
            rows = c.db.query_minute('ping', 48, 'example')
        self.assertEqual(len(rows), 1)

    def test_hour_and_minute_history_do_not_overlap(self):
        now = 40 * 86400 + 1800
        old_hour = 39 * 86400
        c = self.collector
        c.on_result(point(old_hour + 1860))
        c._flush_minute(old_hour + 1860)
        c.db.aggregate_hour(old_hour)
        with patch('core.collector.time.time', return_value=now):
            c.on_result(point(old_hour + 3660, 20))
        c._flush_minute(old_hour + 3660)
        with patch('storage.database.time.time', return_value=now):
            rows = c.db.query_minute('ping', 48, 'example')
        self.assertEqual([(r[0], r[2]) for r in rows], [(old_hour, 10), (old_hour + 3660, 20)])

    def test_hourly_history_supports_all_metric_shapes(self):
        now = 40 * 86400
        old = now - 48 * 3600
        db = self.collector.db
        db.flush_minute(old, {
            'tcp': {'endpoint': {'ms': 12}},
            'dns': {'resolver': {'ms': 13}},
            'traffic': {'all': {'rx_bps': 14, 'tx_bps': 15, 'rx_peak': 16, 'tx_peak': 17}},
        })
        db.aggregate_hour(old)
        db.cleanup(now)
        with patch('storage.database.time.time', return_value=now):
            self.assertEqual(db.query_minute('tcp', 72, 'endpoint'), [(old, 'endpoint', 12)])
            self.assertEqual(db.query_minute('dns', 72, 'resolver'), [(old, 'resolver', 13)])
            self.assertEqual(db.query_minute('traffic', 72), [(old, 14, 15, 16, 17)])

    def test_late_probe_callback_after_close_is_ignored(self):
        self.collector.close()
        self.collector.on_result(point(60))
        self.assertFalse(self.collector.buffer)

    def test_close_preserves_last_hour_for_restart_after_boundary(self):
        now = 40 * 86400
        c = self.collector
        c.on_result(point(now - 60, 10))
        with patch('core.collector.time.time', return_value=now):
            c.close()
        db = Database(config.DB_PATH)
        try:
            self.assertEqual(db.conn.execute('SELECT avg FROM ping_hour').fetchall(), [(10,)])
        finally:
            db.close()

    def test_close_is_idempotent_after_data(self):
        self.collector.on_result(point(60))
        self.collector.close()
        self.collector.close()


if __name__ == '__main__':
    unittest.main()
