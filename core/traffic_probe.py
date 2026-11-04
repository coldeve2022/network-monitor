"""网卡实时速率：系统级 RX/TX 字节数差值，算每秒速率。"""
import threading
import time

import psutil

from core.probe_health import guarded_run
import config
from core.ping_probe import wait_interval


class TrafficProbe(threading.Thread):
    def __init__(self, on_result):
        super().__init__(daemon=True, name="traffic-probe")
        self.on_result = on_result
        self._stop_event = threading.Event()
        self._prev = None
        self._prev_ts = None

    def stop(self):
        self._stop_event.set()

    @guarded_run('traffic')
    def run(self):
        while not wait_interval(self._stop_event, "traffic_interval"):
            ts = time.time()
            mono = time.monotonic()
            snapshot = config.protocol_snapshot()
            interface = snapshot['settings']['traffic_interface']
            if interface:
                cur = psutil.net_io_counters(pernic=True).get(interface)
                if cur is None:
                    self._prev=None
                    raise OSError('selected interface unavailable')
            else:cur = psutil.net_io_counters()
            if interface != getattr(self,'_interface',None):self._prev=None
            self._interface=interface
            if self._prev is not None and mono > self._prev_ts:
                dt = mono - self._prev_ts
                rx_bps = max(0.0, (cur.bytes_recv - self._prev.bytes_recv)) / dt
                tx_bps = max(0.0, (cur.bytes_sent - self._prev.bytes_sent)) / dt
                self.on_result({
                    "ts": ts, "type": "traffic", "probe_revision": snapshot['revision'], "interface": interface or "system_total", "duration": dt,
                    "rx_bytes": max(0, cur.bytes_recv - self._prev.bytes_recv),
                    "tx_bytes": max(0, cur.bytes_sent - self._prev.bytes_sent),
                    "rx_bps": round(rx_bps, 1), "tx_bps": round(tx_bps, 1),
                    "rx_total": cur.bytes_recv, "tx_total": cur.bytes_sent,
                })
            self._prev = cur
            self._prev_ts = mono
