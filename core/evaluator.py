"""健康度判定（优/良/差/断线）与告警（断线/恢复/延迟突增）。"""
import time

import config
from core.quality_policy import group_quality
from core.models import loss_eligible


class Evaluator:
    def __init__(self, on_event):
        self.on_event = on_event            # 回调告警事件 dict
        self._offline_count = 0
        self._was_offline = False
        self._last_alert = {}               # type -> ts，冷却用
        self._prev_avg = {}
        self._target_offline = {}                 # host -> 上一轮平均延迟

    def evaluate(self, ping_results):
        """输入本轮全部 ping 目标结果，返回状态字符串并触发告警。"""
        if not ping_results:
            return "unknown"
        overall=group_quality(ping_results)
        ping_results=[r for r in ping_results if loss_eligible(r)]
        if not ping_results:return 'unknown'
        for r in ping_results:
            offline=r['avg'] is None
            previous=self._target_offline.get(r['host'])
            if offline and previous is not True:self._fire('target_failure',f"{r['name']} ICMP无响应；不代表业务断网",r['host'])
            elif not offline and previous is True:self._fire('target_recover',f"{r['name']} ICMP响应恢复",r['host'])
            self._target_offline[r['host']]=offline
        responded = [r for r in ping_results if r["avg"] is not None]
        if not responded:
            self._offline_count += 1
            if not self._was_offline and self._offline_count >= config.OFFLINE_ROUNDS:
                self._fire("offline", "ICMP探测连续无响应；请结合TCP/DNS判断业务")
                self._was_offline = True
            return "offline"
        self._offline_count = 0
        if self._was_offline:
            self._fire("recover", "ICMP响应恢复")
            self._was_offline = False

        for r in responded:
            prev = self._prev_avg.get(r["host"])
            if prev is not None and r["avg"] - prev > config.get("alert_latency_spike"):
                self._fire("latency_spike",
                           f"{r['name']} 延迟突增：{prev:.0f}ms -> {r['avg']:.0f}ms",r['host'])
            self._prev_avg[r["host"]] = r["avg"]

        return overall

    def _fire(self, etype, message, host=None):
        now=time.time();key=(etype,host)
        notify=now-self._last_alert.get(key,0)>=config.get('alert_cooldown')
        if notify:self._last_alert[key]=now
        self.on_event({'ts':now,'type':etype,'message':message,'host':host,'notify':notify})
