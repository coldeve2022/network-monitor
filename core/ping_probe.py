"""ICMP 延迟/丢包/抖动/标准差探测（PingPlotter 风格统计）。

- 目标动态：基础目标 + traceroute 路径跳，每轮从 provider 拉取；
- 采样频率动态：每轮间隔读运行时设置；
- 统计窗口：当前/平均/最小/最大/标准差/丢包率 基于最近 N 轮滚动计算。
"""
import concurrent.futures
from dataclasses import replace
import statistics
import uuid
import socket
import ipaddress
import threading
import time
from collections import deque

from ping3 import ping

import config
from core.models import ProbeOutcome
from core.probe_health import report_error
from core.scheduler import wait_until_interval


def wait_interval(stop_event, setting, started=None):
    return wait_until_interval(stop_event, lambda: config.get(setting),
        time.monotonic() if started is None else started)


class PingProbe(threading.Thread):
    def __init__(self, on_result, target_provider):
        super().__init__(daemon=True, name="ping-probe")
        self.on_result = on_result
        self._targets = target_provider          # 可调用，返回当前目标列表
        self._stop_event = threading.Event()
        # A system hostname resolver can outlive ping's socket timeout. Keep
        # at most 16 daemon jobs, with one job per host and no retry queue.
        self._inflight = {}
        self._windows = {}                       # 目标唯一键 -> deque(rtt)，最近 stat_window 轮
        self._cursor = 0
        self._prev_rtt = {}

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._round()
            except Exception as exc:
                report_error(self.on_result, 'ping', exc)
            wait_interval(self._stop_event, "ping_interval", started)

    def _submit(self, host):
        future = concurrent.futures.Future()
        def work():
            try:
                started_at=time.time()
                result=self._ping_once(host)
                if not isinstance(result,ProbeOutcome):result=ProbeOutcome('ok' if result is not None else 'sent_timeout',result)
                future.set_result(replace(result,started_at=started_at,finished_at=time.time()))
            except BaseException as exc:
                future.set_exception(exc)
        threading.Thread(target=work, daemon=True, name="ping-worker").start()
        self._inflight[host] = future
        return future

    def _ping_once(self, host):
        try:
            try: ipaddress.IPv4Address(host)
            except ValueError: host = socket.gethostbyname(host)
            r = ping(host, timeout=config.get("ping_timeout"), unit="ms")
            if isinstance(r, (int, float)) and not isinstance(r, bool):
                return ProbeOutcome('ok', r)
            return ProbeOutcome('sent_timeout' if r is None else 'local_error', error_code=None if r is None else 'icmp_error')
        except socket.gaierror as exc:
            return ProbeOutcome('resolution_error', error_code=str(exc.errno))
        except Exception as exc:
            return ProbeOutcome('local_error', error_code=type(exc).__name__)

    @staticmethod
    def _target_key(t):
        # 同一 IP 可能同时是基础目标和路径跳，必须分开维护统计窗口，避免互相污染。
        return (t["host"], t.get("target_id"), t.get("kind"), t.get("hop"), t.get("trace_of"))

    def _round(self):
        ts = time.time()
        targets = self._targets()
        if not targets:
            return
        window = max(int(config.get("stat_window", 60)), 1)
        keys = {self._target_key(t) for t in targets}
        self._windows = {k: v for k, v in self._windows.items() if k in keys}
        self._prev_rtt = {k: v for k, v in self._prev_rtt.items() if k in keys}
        self._inflight = {host: fut for host, fut in self._inflight.items() if not fut.done()}
        host_list = list(dict.fromkeys(t["host"] for t in targets))
        offset = self._cursor % len(host_list)
        hosts = dict.fromkeys(host_list[offset:] + host_list[:offset])
        self._cursor += 16
        pending = [host for host in hosts if host not in self._inflight]
        futures = {}
        samples = {host: ProbeOutcome("skipped", error_code="capacity_or_inflight") for host in hosts}
        deadline = time.monotonic() + config.get("ping_timeout") + 0.5
        while pending or futures:
            if self._stop_event.is_set():
                return
            for host, future in list(futures.items()):
                if future.done():
                    try:
                        samples[host] = future.result()
                    except Exception:
                        samples[host] = ProbeOutcome("local_error", error_code="worker_error")
                    del futures[host]
            self._inflight = {host: future for host, future in self._inflight.items()
                              if not future.done()}
            if time.monotonic() >= deadline:
                break
            while pending and len(self._inflight) < 16:
                host = pending.pop(0)
                futures[host] = self._submit(host)
            if not futures:
                break
            self._stop_event.wait(min(0.02, max(0, deadline - time.monotonic())))
        if self._stop_event.is_set():
            return
        results = []
        for t in targets:
            outcome = samples[t["host"]]
            if not isinstance(outcome, ProbeOutcome):
                outcome = ProbeOutcome('ok' if outcome is not None else 'sent_timeout', outcome)
            rtt = outcome.value
            eligible = outcome.state in ('ok', 'sent_timeout')
            key = self._target_key(t)
            win = self._windows.get(key)
            if win is None or win.maxlen != window:
                old = list(win) if win else []
                win = deque(old, maxlen=window)
                self._windows[key] = win
            if eligible: win.append(rtt)
            ok = [v for v in win if v is not None]
            loss = round((1 - len(ok) / len(win)) * 100, 1) if eligible and win else None
            std = round(statistics.pstdev(ok), 1) if len(ok) > 1 else 0.0
            prev = self._prev_rtt.get(key)
            jitter = round(abs(rtt - prev), 1) if (rtt is not None and prev is not None) else None
            self._prev_rtt[key] = rtt
            results.append({
                "ts": ts, "type": "ping", "state": outcome.state, "error_code": outcome.error_code,
                "round_id": str(ts), "sample_id": str(ts) + ':' + t["host"],
                "scheduled_at":ts,"started_at":outcome.started_at,"finished_at":outcome.finished_at,
                "config_revision": t.get("config_revision", 0), "target_id": t.get("target_id", t["host"]), "name": t["name"], "host": t["host"],
                "kind": t["kind"], "hop": t["hop"], "trace_of": t["trace_of"],
                "avg": round(rtt, 1) if rtt is not None else None,
                "min": round(min(ok), 1) if ok else None,
                "max": round(max(ok), 1) if ok else None,
                "std": std, "loss": loss, "jitter": jitter,
            })
        if not self._stop_event.is_set():
            self.on_result({"ts": ts, "type": "ping_round", "targets": results})
