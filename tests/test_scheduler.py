import concurrent.futures
import socket
import struct
import threading
import unittest
from unittest.mock import Mock, patch
from core.ping_probe import PingProbe
from core.tcp_probe import TcpProbe
from core.traceroute import traceroute


class SchedulerTests(unittest.TestCase):
    def test_actual_icmp_wire_checksum_is_valid(self):
        sock=Mock();sock.recvfrom.side_effect=socket.timeout
        with patch('core.traceroute.socket.socket',return_value=sock),patch('core.traceroute.time.time',return_value=1000),patch('core.traceroute.os.getpid',return_value=123):
            traceroute('192.0.2.1',max_hops=1,timeout=.001)
        packet=sock.sendto.call_args.args[0]
        total=sum(struct.unpack('!8H',packet))
        while total>>16:total=(total&65535)+(total>>16)
        self.assertEqual(total,65535,'invalid ICMP checksum in actual emitted bytes')

    def test_capacity_limit_does_not_invent_packet_loss(self):
        targets=[dict(host=f'192.0.2.{i}',name=str(i),kind='base',hop=None,trace_of=None) for i in range(1,18)]
        rows=[];probe=PingProbe(rows.append,lambda:targets)
        probe._inflight={t['host']:concurrent.futures.Future() for t in targets[:16]}
        probe._round()
        self.assertTrue(all(r.get('state')=='skipped' for r in rows[0]['targets']))
        self.assertTrue(all(r['loss'] is None for r in rows[0]['targets']))

    def test_stop_during_tcp_resolution_has_bounded_join(self):
        entered=threading.Event();release=threading.Event()
        def blocked(*a,**kw): entered.set();release.wait(4);raise OSError('released')
        probe=TcpProbe(lambda row:None)
        with patch('core.tcp_probe.socket.getaddrinfo',side_effect=blocked),patch('core.tcp_probe.wait_interval',return_value=False):
            probe.start()
            try:
                self.assertTrue(entered.wait(1))
                probe.stop();probe.join(.5)
                self.assertFalse(probe.is_alive(),'uncancellable resolver leaked into probe shutdown')
            finally:release.set();probe.stop();probe.join(1)
