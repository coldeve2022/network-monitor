"""路径追踪管理（PingPlotter 核心机制）：
对配置目标做 UDP traceroute，把路径每一跳动态加入 Collector 的持续监测；
用户可通过运行时开关关闭，关闭后 2 秒内自动清理已加入的跳目标。"""
import threading
import time

import config
from core.traceroute import traceroute
from core.scheduler import BoundedJobs


class TraceManager(threading.Thread):
    def __init__(self, collector):
        super().__init__(daemon=True, name="trace-manager")
        self.collector = collector
        self._stop_event = threading.Event()
        self._jobs=BoundedJobs(capacity=8)

    def stop(self):
        self._stop_event.set()

    def _cancelled(self):
        return self._stop_event.is_set() or not config.get("enable_traceroute")

    def _clear(self):
        if self.collector.traces:
            self.collector.clear_traces()
            self.collector.broadcast({"type": "traces_cleared"})

    def run(self):
        last_network = time.monotonic()
        last_trace = None
        while not self._stop_event.is_set():
            if time.monotonic()-last_network >= 5:
                try:self.collector.refresh_network()
                except Exception:self.collector.report_probe_error('network','refresh_failed')
                last_network=time.monotonic()
            if not config.get("enable_traceroute"):
                self._clear()
                last_trace = None
            elif last_trace is None or time.monotonic() - last_trace >= config.get("trace_interval"):
                self._trace_all()
                last_trace = time.monotonic()
            self._stop_event.wait(0.2)

    def _trace_all(self):
        snapshot=config.protocol_snapshot();targets=snapshot['settings']['trace_targets'];revision=snapshot['revision']
        with self.collector._lock:
            retired=[t for t in self.collector.traces if t not in targets]
            if retired:
                for target in retired:self.collector.traces.pop(target,None)
                self.collector.config_revision+=1;self.collector._rebuild_targets()
        for target in targets:
            if self._cancelled():self._clear();return
            def work(destination):
                return traceroute(destination,max_hops=config.TRACE_MAX_HOPS,timeout=config.TRACE_TIMEOUT,max_idle=config.TRACE_IDLE_STOP,cancelled=lambda:self._cancelled() or config.PROBE_REVISION!=revision)
            for _,hops in self._jobs.collect([target],lambda t:(revision,t),work,config.TRACE_MAX_HOPS*(config.TRACE_TIMEOUT+.2)+3,self._stop_event):
                if self._cancelled():self._clear();return
                if config.PROBE_REVISION==revision and hops is not None:self.collector.update_trace(target,hops,require_enabled=True)
