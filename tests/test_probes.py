import socket
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch

import config
from core.ping_probe import PingProbe
from core.tcp_probe import TcpProbe
from core.dns_probe import DnsProbe
from core.traffic_probe import TrafficProbe
from core.trace_manager import TraceManager
from core.traceroute import traceroute
from core.ping_probe import wait_interval


def target(kind='base', trace_of=None):
    return dict(host='1.2.3.4', name=kind, kind=kind, hop=1, trace_of=trace_of)


class ProbeTests(unittest.TestCase):
    def test_more_than_sixteen_hosts_are_sampled_without_starvation(self):
        rows = []
        targets = [{**target(), 'host': f'192.0.2.{i}'} for i in range(1, 34)]
        probe = PingProbe(rows.append, lambda: targets)
        with patch('core.ping_probe.ping', return_value=10) as ping:
            probe._round()
        self.assertEqual(ping.call_count, 33)
        self.assertTrue(all(row['avg'] == 10 for row in rows[0]['targets']))
        probe.stop()

    def test_hung_ping_has_bounded_round_and_shutdown_without_duplicate_jobs(self):
        release = threading.Event()
        entered = threading.Event()
        rows = []
        def stuck(*args, **kwargs):
            entered.set()
            release.wait(5)
            return 10
        probe = PingProbe(rows.append, lambda: [target()])
        getter = config.get
        with patch('core.ping_probe.ping', side_effect=stuck) as ping, patch('core.ping_probe.config.get', side_effect=lambda key, default=None: .01 if key == 'ping_timeout' else getter(key, default)):
            thread = threading.Thread(target=probe._round, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            thread.join(1)
            try:
                self.assertFalse(thread.is_alive(), 'one resolver must not freeze publication')
                probe._round()
                self.assertEqual(ping.call_count, 1)
                self.assertEqual(rows[0]['targets'][0]['avg'], None)
                probe.start()
                probe.stop()
                probe.join(.5)
                self.assertFalse(probe.is_alive(), 'shutdown must not wait for stuck daemon I/O')
            finally:
                release.set()
                probe.stop()

    def test_app_initialization_failure_stops_started_probes_and_closes_storage(self):
        import main
        collector = Mock()
        workers = [Mock() for _ in range(5)]
        with patch.object(main, '_port_in_use', return_value=False), patch.object(main, 'detect_gateway'), patch.object(main, 'Collector', return_value=collector), patch.object(main, 'PingProbe', return_value=workers[0]), patch.object(main, 'TcpProbe', return_value=workers[1]), patch.object(main, 'DnsProbe', return_value=workers[2]), patch.object(main, 'TrafficProbe', return_value=workers[3]), patch.object(main, 'TraceManager', return_value=workers[4]), patch.object(main, 'create_app', side_effect=OSError('assets unavailable')):
            with self.assertRaises(OSError):
                main.main()
        for worker in workers:
            worker.stop.assert_called_once()
            worker.join.assert_called_once()
        collector.close.assert_called_once()

    def test_trace_commit_rechecks_enable_under_collector_lock(self):
        from core.collector import Collector
        collector = Collector.__new__(Collector)
        collector._lock = threading.Lock()
        collector._closed = False
        collector._base = []
        collector.traces = {}
        collector.broadcast = Mock()
        with patch('core.collector.config.get', return_value=False):
            accepted = collector.update_trace('a', ['1.2.3.4'], require_enabled=True)
        self.assertFalse(accepted)
        self.assertEqual(collector.traces, {})
        collector.broadcast.assert_not_called()

    def test_shortening_interval_wakes_an_existing_long_wait(self):
        interval = [3600]
        finished = threading.Event()
        stop = threading.Event()
        def wait():
            wait_interval(stop, 'dns_interval')
            finished.set()
        with patch('core.ping_probe.config.get', side_effect=lambda key: interval[0]):
            thread = threading.Thread(target=wait)
            thread.start()
            interval[0] = 0
            self.assertTrue(finished.wait(1))
            stop.set()
            thread.join(1)

    def test_shutdown_joins_every_writer_before_collector_close(self):
        import main
        events = []
        workers = []
        for name in ['ping', 'tcp', 'dns', 'traffic', 'trace']:
            worker = Mock()
            worker.stop.side_effect = lambda name=name: events.append(('stop', name))
            worker.join.side_effect = lambda timeout=None,name=name: events.append(('join', name))
            workers.append(worker)
        collector = Mock()
        collector.close.side_effect = lambda: events.append(('close', 'db'))
        with patch.object(main, '_port_in_use', return_value=False), patch.object(main, 'detect_gateway', return_value=None), patch.object(main, 'Collector', return_value=collector), patch.object(main, 'PingProbe', return_value=workers[0]), patch.object(main, 'TcpProbe', return_value=workers[1]), patch.object(main, 'DnsProbe', return_value=workers[2]), patch.object(main, 'TrafficProbe', return_value=workers[3]), patch.object(main, 'TraceManager', return_value=workers[4]), patch.object(main, 'create_app'), patch.object(main.threading, 'Timer'), patch.object(main.uvicorn, 'run'):
            main.main()
        self.assertEqual(events[:5], [('stop', n) for n in ['ping', 'tcp', 'dns', 'traffic', 'trace']])
        self.assertEqual(events[5:10], [('join', n) for n in ['ping', 'tcp', 'dns', 'traffic', 'trace']])
        self.assertEqual(events[-1], ('close', 'db'))

    def test_stopped_threads_can_be_joined(self):
        probes = [PingProbe(Mock(), lambda: []), TcpProbe(Mock()),
                  DnsProbe(Mock()), TrafficProbe(Mock()), TraceManager(Mock())]
        for probe in probes:
            probe.stop()
            probe.start()
            probe.join(1)
            self.assertFalse(probe.is_alive())

    def test_stopping_ping_discards_an_inflight_result(self):
        entered = threading.Event()
        release = threading.Event()
        results = []
        def ping(*args, **kwargs):
            entered.set()
            release.wait(2)
            return 10
        probe = PingProbe(results.append, lambda: [target()])
        with patch('core.ping_probe.ping', side_effect=ping):
            probe.start()
            self.assertTrue(entered.wait(1))
            probe.stop()
            release.set()
            probe.join(1)
        self.assertFalse(probe.is_alive())
        self.assertEqual(results, [])

    def test_ping_shares_wire_sample_but_retains_row_identity(self):
        rows = []
        probe = PingProbe(rows.append, lambda: [target(), target('hop', 'a'), target('hop', 'b')])
        try:
            with patch('core.ping_probe.ping', return_value=10) as ping:
                probe._round()
            self.assertEqual(ping.call_count, 1)
            self.assertEqual(len(rows[0]['targets']), 3)
            self.assertEqual(len(probe._windows), 3)
        finally:
            probe.stop()

    def test_trace_runs_once_on_startup(self):
        collector = Mock(traces={},_lock=threading.Lock(),config_revision=0)
        manager = TraceManager(collector)
        calls = []
        def trace():
            calls.append(1)
            if len(calls) > 1:
                manager.stop()
        with patch('core.trace_manager.config.get', side_effect=lambda k: True if k == 'enable_traceroute' else 300), patch.object(manager, '_trace_all', side_effect=trace):
            manager.start()
            time.sleep(.1)
            manager.stop()
            try:
                manager.join(1)
            except TypeError:
                pass
        self.assertEqual(len(calls), 1)

    def test_disabled_inflight_trace_does_not_publish(self):
        collector = Mock(traces={},_lock=threading.Lock(),config_revision=0)
        manager = TraceManager(collector)
        enabled = [True]
        def trace(*args, **kwargs):
            enabled[0] = False
            return ['1.2.3.4']
        with patch('core.trace_manager.config.get', side_effect=lambda k: enabled[0]), patch('core.trace_manager.traceroute', side_effect=trace):
            manager._trace_all()
        collector.update_trace.assert_not_called()

    def test_long_trace_wait_observes_disable_within_two_seconds(self):
        collector = Mock(traces={'a': ['1.2.3.4']})
        manager = TraceManager(collector)
        enabled = [True]
        traced = threading.Event()
        with patch('core.trace_manager.config.get', side_effect=lambda k: enabled[0] if k == 'enable_traceroute' else 3600), patch.object(manager, '_trace_all', side_effect=traced.set):
            manager.start()
            self.assertTrue(traced.wait(1))
            enabled[0] = False
            deadline = time.monotonic() + 1.5
            while not collector.clear_traces.called and time.monotonic() < deadline:
                time.sleep(.01)
            manager.stop()
            try:
                manager.join(1)
            except TypeError:
                pass
        collector.clear_traces.assert_called()


def packet(kind, seq, outer_words=5, inner_words=5, ident=123):
    outer = bytes([0x40 | outer_words]) + bytes(outer_words * 4 - 1)
    echo = struct.pack('!BBHHH', 0 if kind == 0 else 8, 0, 0, ident, seq)
    if kind == 0:
        return outer + echo
    inner = bytes([0x40 | inner_words]) + bytes(inner_words * 4 - 1)
    return outer + bytes([kind]) + bytes(7) + inner + echo


class TracerouteTests(unittest.TestCase):
    def test_cancellation_interrupts_receive_without_publishing_partial_hop(self):
        cancelled = threading.Event()
        sock = Mock()
        def receive(*args):
            cancelled.set()
            raise socket.timeout()
        sock.recvfrom.side_effect = receive
        with patch('core.traceroute.socket.socket', return_value=sock):
            result = traceroute('1.2.3.4', timeout=60, cancelled=cancelled.is_set)
        self.assertEqual(result, [])
        self.assertLessEqual(sock.settimeout.call_args.args[0], .2)
        sock.close.assert_called_once()

    def run_trace(self, replies, **kwargs):
        sock = Mock()
        sock.recvfrom.side_effect = replies
        with patch('core.traceroute.socket.socket', return_value=sock), patch('core.traceroute.os.getpid', return_value=123):
            result = traceroute('1.2.3.4', **kwargs)
        return result, sock

    def test_variable_ip_headers_and_stale_reply_do_not_skip_ttl(self):
        replies = [(packet(0, 99), ('1.2.3.4', 0)),
                   (packet(11, 1, 6, 6), ('9.8.7.6', 0)),
                   (packet(0, 2, 6), ('1.2.3.4', 0))]
        result, sock = self.run_trace(replies, max_hops=2)
        self.assertEqual(result, ['9.8.7.6', '1.2.3.4'])
        self.assertEqual(sock.sendto.call_count, 2)

    def test_unrelated_packets_consume_same_hop_timeout_budget(self):
        ticks = iter([0, 0, .4, .8, 1.1, 1.1, 1.1])
        with patch('core.traceroute.time.monotonic', side_effect=lambda: next(ticks)):
            result, sock = self.run_trace([(packet(0, 99), ('8.8.8.8', 0))] * 4,
                                          max_hops=1, timeout=1)
        self.assertEqual(result, [None])
        self.assertLessEqual(sock.recvfrom.call_count, 3)

    def test_socket_closed_on_bind_error(self):
        sock = Mock()
        sock.bind.side_effect = OSError('denied')
        with patch('core.traceroute.socket.socket', return_value=sock), self.assertRaises(OSError):
            traceroute('1.2.3.4')
        sock.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
