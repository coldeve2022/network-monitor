"""采集调度器：目标管理（基础目标 + traceroute 路径跳）、统一时间戳、
内存缓冲、分钟聚合、WebSocket 广播、健康度判定。"""
import json
import copy
import logging
import uuid
import math
import tempfile
from pathlib import Path
import threading
import time
from collections import deque
from queue import Queue, Full, Empty

import config
from core.evaluator import Evaluator
from core.models import normalize_sample, loss_eligible
from core.quality_policy import thresholds, ping_quality
from core.targets import base_target, valid_host
from core.probe_health import ProbeHealth
from core.statistics import accumulate, summarize, merge_stats, quality_summary
from storage.database import Database
from storage.writer import StorageWriter


class Collector:
    def __init__(self):
        self.db = Database(config.DB_PATH)
        self.buffer = deque(maxlen=config.BUFFER_MAXLEN)   # 原始秒级点
        self.events = deque(maxlen=200)
        self.outbox = Queue(maxsize=4096)
        self.outbox_dropped = 0                               # 广播消息队列，server 消费
        self._subs = []                                     # WebSocket 连接
        self._lock = threading.Lock()
        self._target_write_lock = threading.Lock()
        self._minutes = {}
        self._latest_by_target = {}
        self._minute_key = None
        self.session_id = uuid.uuid4().hex
        self._sequence = 0
        self.time_epoch = 0
        self._receipt_wall = None
        self._receipt_mono = None
        self.probe_health = ProbeHealth()
        self._pending_raw = []
        self._live_by_target = {}
        self._closed = False
        self._last_status = "unknown"
        self._start_ts = time.time()
        self._start_mono = time.monotonic()
        self.evaluator = Evaluator(self._on_event)
        self.targets = []                                   # 当前 ping 目标（基础 + 路径跳）
        self.traces = {}                                    # trace 目标 -> [ip 或 None]
        self._probe_revision = config.PROBE_REVISION
        self.config_revision = 0
        self.configuration_error = None
        self.network_context = {}
        self._base = self._build_base()
        self._init_targets()
        self.db.register_session(self.session_id,self._start_ts)
        # Resume persisted counts once before live acquisition; fresh minutes need no disk reads.
        current=int(time.time()//60)*60
        persisted=self.db.load_minute_stats(current)
        if persisted:self._minutes[current]=persisted
        self._persist_retry_lock=threading.Lock()
        self._dirty_minutes=set()
        self._recovery_raw=deque(maxlen=16000)
        self._writer=StorageWriter(self.db,self._storage_error,on_success=lambda: self.probe_health.record_sample('storage',time.time()))

    @staticmethod
    def _build_base():
        base = []
        for t in config.PING_TARGETS:
            if t["host"] == "auto":
                continue
            base.append(base_target(t['name'],t['host']))
        return base

    def _init_targets(self):
        """首次运行把内置目标写入 targets.json；之后以 json 为唯一事实来源，
        用户增删的目标重启后保持。"""
        path = config.TARGETS_FILE
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data,dict) or not isinstance(data.get('targets'),list):raise ValueError('目标文件结构无效')
                custom = []
                seen=set()
                for i in data['targets']:
                    host = str(i.get("host", "")).strip()
                    if not host:
                        continue
                    target=base_target(i.get('name'),host,i.get('target_id') or uuid.uuid5(uuid.NAMESPACE_URL,'network-monitor:'+host).hex)
                    if target['host'] not in seen:custom.append(target);seen.add(target['host'])
                self._base = custom
                self.config_revision = int(data.get('revision',0))
                self._rebuild_targets()
                return
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                self.configuration_error = '目标配置读取失败，原文件已保留：' + type(exc).__name__
                self._rebuild_targets()
                return
        self._save_custom_targets()
        self.config_revision += 1
        self._rebuild_targets()

    def _save_custom_targets(self, targets=None):
        custom = [{"name": t["name"], "host": t["host"], "target_id": t.get('target_id')}
                  for t in (self._base if targets is None else targets)
                  if not t.get("protected")]
        path = config.TARGETS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=path.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"targets": custom,"revision":self.config_revision+1}, stream, ensure_ascii=False, indent=2)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def set_gateway(self, gw):
        with self._lock:
            previous=next((t['host'] for t in self._base if t.get('protected')),None)
            if gw==previous:return
            self._base=[t for t in self._base if not t.get('protected')]
            if gw:
                target=base_target('网关',gw,'gateway:'+gw);target['protected']=True
                self._base.insert(0,target)
            self.config_revision+=1
            self._rebuild_targets()

    def refresh_network(self):
        from core.network_context import snapshot
        context=snapshot()
        previous=self.network_context
        self.network_context=context
        self.set_gateway(context['gateway'])
        if previous and context!=previous:
            self.broadcast({'type':'network_changed','ts':time.time(),'context':context})

    def add_target(self,name,host):
        try:host=valid_host(host)
        except ValueError as exc:return False,str(exc)
        name=(name or host).strip()
        with self._target_write_lock:
            with self._lock:
                if self.configuration_error:return False,'目标文件需要恢复，原文件未覆盖'
                if any(t['host']==host for t in self._base):return False,'该目标已存在'
                candidate=[t for t in self._base if not t.get('protected')]+[base_target(name,host)]
            try:self._save_custom_targets(candidate)
            except OSError:return False,'目标保存失败，原设置保持不变'
            with self._lock:
                self._base=[t for t in self._base if t.get('protected')]+candidate
                self.config_revision+=1;self._rebuild_targets()
        return True,'已添加'

    def remove_target(self,host):
        with self._target_write_lock:
            with self._lock:
                if self.configuration_error:return False,'目标文件需要恢复，原文件未覆盖'
                target=next((t for t in self._base if t['host']==host),None)
                if target is None:return False,'目标不存在'
                if target.get('protected'):return False,'网关为系统目标，不可删除'
                candidate=[t for t in self._base if not t.get('protected') and t['host']!=host]
            try:self._save_custom_targets(candidate)
            except OSError:return False,'目标保存失败，原设置保持不变'
            with self._lock:
                self._base=[t for t in self._base if t.get('protected')]+candidate
                self.config_revision+=1
                self._latest_by_target.get('ping',{}).pop(host,None)
                self.buffer=deque((i for i in self.buffer if i.get('host')!=host),maxlen=config.BUFFER_MAXLEN)
                self._live_by_target={k:v for k,v in self._live_by_target.items() if not v or v[-1].get('host')!=host}
                self._rebuild_targets()
        return True,'已删除'

    def update_trace(self, target, hops, require_enabled=False):
        """traceroute 结果更新：替换该目标对应的路径跳监测项。"""
        with self._lock:
            if require_enabled and (self._closed or not config.get("enable_traceroute")):
                return False
            if require_enabled and target not in config.TRACE_TARGETS:return False
            if self.traces.get(target)!=hops:self.config_revision+=1
            self.traces[target] = hops
            self._rebuild_targets()
            if require_enabled:
                self.broadcast({"type": "trace", "target": target, "hops": hops})
            return True

    def clear_traces(self):
        """清空所有路径跳目标（用户关闭 traceroute 时调用）。"""
        with self._lock:
            if self.traces:self.config_revision+=1
            self.traces = {}
            self._rebuild_targets()

    def compute_summary(self, hours=1):
        hosts={t['host'] for t in self.ping_targets() if t['kind']=='base'}
        stats=merge_stats(self.db.query_stats('ping',hours,host) for host in hosts)
        return {**quality_summary(stats),'uptime':time.monotonic()-self._start_mono,'window_seconds':hours*3600}

    def compute_target_summaries(self, hours=1, buffer=None, targets=None):
        """每个目标最近 N 分钟的聚合摘要（MOS/CV/可用率/均值等），用于按行展示。
        用内存 buffer 实时计算（秒级数据，启动后立即有结果），不需要等分钟聚合。"""
        import statistics as _stats
        if buffer is None or targets is None:
            with self._lock:
                buffer = self.live_samples()
                targets = list(self.targets)
        window_sec = min(hours * 3600, 600)   # buffer 最多 10 分钟 = 600s
        now = time.time()
        cutoff = now - window_sec
        # 按 host 收集 buffer 中所有 ping 点
        per_host = {}
        samples = {}
        for item in buffer:
            if item.get("type") != "ping" or not loss_eligible(item):
                continue
            if item.get("ts", 0) < cutoff or item.get('ts',0)>now+5 or item.get('time_epoch',self.time_epoch)!=self.time_epoch:
                continue
            key = (item["host"], item["ts"])
            previous = samples.get(key)
            if previous is None or item.get("kind") == "base":
                samples[key] = item
        for item in samples.values():
            per_host.setdefault(item["host"], []).append(item)
        summaries = {}
        for tgt in targets:
            host = tgt["host"]
            items = per_host.get(host, [])
            if not items:
                continue
            stats={}
            for item in items:accumulate(stats,item)
            summaries[host]={**quality_summary(stats), 'coverage_start':min(i['ts'] for i in items),
                'coverage_end':max(i['ts'] for i in items),'window_seconds':window_sec}
        return summaries

    def _rebuild_targets(self):
        kept = list(self._base)
        for tgt, hops in self.traces.items():
            for i, ip in enumerate(hops, start=1):
                if ip is None:
                    continue
                kept.append({"name": f"{tgt}·跳{i}", "host": ip, "kind": "hop",
                             "trace_of": tgt, "hop": i})
        self.targets = [{**t,'config_revision':self.config_revision,'target_id':t.get('target_id') or 'hop:'+str(t.get('trace_of'))+':'+str(t.get('hop'))+':'+t['host']} for t in kept]

        active_hosts={t['host'] for t in self.targets}
        active_ids={t['target_id'] for t in self.targets}|active_hosts
        self._latest_by_target['ping']={h:i for h,i in self._latest_by_target.get('ping',{}).items() if h in active_hosts and i.get('target_id',h) in active_ids}
        self._live_by_target={k:v for k,v in self._live_by_target.items() if k[0]!='ping' or k[1] in active_ids}
        self.evaluator._prev_avg={h:v for h,v in self.evaluator._prev_avg.items() if h in active_hosts}
        self.evaluator._target_offline={h:v for h,v in self.evaluator._target_offline.items() if h in active_hosts}

    def protocols_changed(self):
        with self._lock:self._sync_protocols_locked()

    def _sync_protocols_locked(self):
        revision=config.PROBE_REVISION
        if revision==self._probe_revision:return
        self._probe_revision=revision
        for protocol in ('tcp','dns','traffic'):self._latest_by_target.pop(protocol,None)
        self._live_by_target={k:v for k,v in self._live_by_target.items() if k[0]=='ping'}
        retained={t:h for t,h in self.traces.items() if t in config.TRACE_TARGETS}
        if retained!=self.traces:self.traces=retained;self.config_revision+=1;self._rebuild_targets()
        self._broadcast({'type':'probes_changed','probe_revision':revision})

    def _active_ping(self,item):
        if item.get('config_revision',self.config_revision)!=self.config_revision:return False
        if item.get('target_id') is None:return True
        return any(t['target_id']==item['target_id'] for t in self.targets)

    def ping_targets(self):
        with self._lock:
            return list(self.targets)

    def subscribe(self, ws):
        with self._lock:
            self._subs.append(ws)

    def unsubscribe(self, ws):
        with self._lock:
            if ws in self._subs:
                self._subs.remove(ws)

    # ---- 数据入口 ----
    def on_result(self, data):
        with self._lock:
            if self._closed:
                return
            self._check_clock()
            self._sync_protocols_locked()
            if data['type'] in ('tcp','dns','traffic'):
                if data.get('probe_revision',self._probe_revision)!=self._probe_revision:return
                data={**data,'probe_revision':self._probe_revision}
            if data["type"] == "ping_round":
                # Storage and summary APIs are keyed by host; prefer the base
                # probe when the same host is also present as one or more hops.
                data = {**data, "targets": [self._stamp(item) for item in data["targets"] if self._active_ping(item)]}
                selected = {}
                for item in data["targets"]:
                    host = item["host"]
                    if host not in selected or item.get("kind") == "base":
                        selected[host] = item
                base = [r for r in selected.values() if r.get("kind") == "base"]
                self._last_status = self.evaluator.evaluate(base)
                self.probe_health.record_round('ping',base)
                for item in data["targets"]:
                    self.buffer.append(item)
                    self._update_latest(item)
                    self._broadcast(item)
                for item in selected.values():
                    self._accumulate(item)
                self._broadcast({"ts": data["ts"], "type": "status",
                                 "status": self._last_status, "round_complete":True})
            else:
                if data['type']=='ping' and not self._active_ping(data):return
                self._record(data)
            self._maybe_flush(data["ts"])

    def _check_clock(self):
        wall,mono=time.time(),time.monotonic()
        if self._receipt_wall is not None and abs((wall-self._receipt_wall)-(mono-self._receipt_mono))>5:
            if self._minute_key is not None:
                self._flush_minute(self._minute_key,wait=False)
                self._writer.submit('aggregate_hour',(self._minute_key//3600*3600,))
            self.time_epoch+=1
            self._minute_key=None
            self._live_by_target.clear()
            self._broadcast({'type':'clock_changed','time_epoch':self.time_epoch,'ts':wall})
        self._receipt_wall,self._receipt_mono=wall,mono

    def _stamp(self, item):
        item = normalize_sample(item)
        if item['type']=='ping':item['quality']=ping_quality(item)
        self._sequence += 1
        item.update(session_id=self.session_id, sequence=self._sequence,time_epoch=self.time_epoch)
        basis=item.get('started_at') if isinstance(item.get('started_at'),(int,float)) else item['ts']
        if basis>time.time()+5:item['time_epoch']=max(0,self.time_epoch-1)
        self.probe_health.record_sample(item['type'], item['ts'],item['state'])
        self._pending_raw.append(item)
        if len(self._pending_raw)>16000:
            self._pending_raw=self._pending_raw[-16000:]
            self.report_probe_error(item['type'],'raw_storage_backlog')
        identity=(item['type'],item['target_id'])
        live=self._live_by_target.setdefault(identity,deque(maxlen=1200))
        if live and live[-1]['ts']==item['ts']:
            if item.get('kind')=='base':live[-1]=item
        else:live.append(item)
        while live and live[0]['ts']<item['ts']-600:live.popleft()
        return item

    def _record(self, item):
        item = self._stamp(item)
        self.buffer.append(item)
        self._update_latest(item)
        self._accumulate(item)
        self._broadcast(item)

    def _update_latest(self, item):
        kind = item["type"]
        key_field = {"ping": "host", "tcp": "name", "dns": "server"}.get(kind)
        if key_field is None and kind != "traffic":
            return
        key = item[key_field] if key_field else "all"
        latest = self._latest_by_target.setdefault(kind, {})
        previous = latest.get(key)
        if (previous is None or item.get('time_epoch',0)>previous.get('time_epoch',0) or item.get('time_epoch',0)==previous.get('time_epoch',0) and (item["ts"] > previous["ts"] or
                (item["ts"] == previous["ts"] and
                 (kind != "ping" or previous.get("kind") != "base" or item.get("kind") == "base")))):
            latest[key] = dict(item)

    # ---- 广播 ----
    def _broadcast(self, msg):
        try:self.outbox.put_nowait(msg)
        except Full:
            try:self.outbox.get_nowait()
            except Empty:pass
            self.outbox_dropped+=1
            try:self.outbox.put_nowait(msg)
            except Full:self.outbox_dropped+=1

    def broadcast(self,msg):
        self._broadcast(msg)

    # ---- 分钟聚合 ----
    def _accumulate(self, item):
        if item["type"] == "ping" and not loss_eligible(item):
            return
        key = int(item["ts"] // 60) * 60
        historical_epoch=item.get('time_epoch',self.time_epoch)<self.time_epoch
        if self._minute_key is None:
            self._minute_key = int(time.time()//60)*60 if historical_epoch else key
        elif key > self._minute_key and not historical_epoch:
            self._flush_minute(self._minute_key,wait=False)
            self._roll_hour_if_needed(key)
            self._minute_key = key
            # Compact sufficient statistics retain late-update accuracy without
            # retaining a day's raw packets. Older data is outside minute retention.
            cutoff = key - config.MINUTE_RETENTION_HOURS * 3600
            self._minutes = {k: v for k, v in self._minutes.items() if k >= cutoff}
        if key < self._minute_key - config.MINUTE_RETENTION_HOURS * 3600:
            return
        kind = item["type"]
        key_field = {"ping": "host", "tcp": "name", "dns": "server"}.get(kind)
        if key_field is None and kind != "traffic":
            return
        target = item.get('storage_key',item[key_field]) if key_field else "all"
        if key not in self._minutes:
            self._minutes[key] = {}
        bucket = self._minutes[key]
        stats = bucket.setdefault((kind, target), {"count": 0, "ok": 0})
        accumulate(stats,item)
        if key < self._minute_key or historical_epoch:
            self._flush_minute(key,wait=False)
            hour = key // 3600 * 3600
            if hour < self._minute_key // 3600 * 3600:
                self._writer.submit('aggregate_hour',(hour,))

    def _roll_hour_if_needed(self, new_minute_key):
        """跨入新小时后，聚合刚结束的小时并执行一次保留清理。"""
        old_hour = self._minute_key // 3600 * 3600
        if new_minute_key // 3600 * 3600 > old_hour:
            self._writer.submit('aggregate_hour',(old_hour,))
            self._writer.submit('cleanup',(time.time(),))

    def _flush_minute(self, key, wait=True):
        agg = {"ping": {}, "tcp": {}, "dns": {}, "traffic": {}}
        for (kind, target), stats in self._minutes.get(key, {}).items():
            if not stats.get('count'):continue
            a=summarize(stats,kind)
            if kind=='ping':agg[kind][target]={key:a[key] if a[key] is not None else (0.0 if key in ('jitter','std') else None) for key in ('avg','min','max','loss','jitter','std')}
            elif kind in ('tcp','dns'):agg[kind][target]={'ms':a['ms']}
            elif kind=='traffic':agg[kind][target]=a
        accepted=self._writer.submit('flush_minute',(key,agg),{'stats':copy.deepcopy(self._minutes.get(key,{}))},wait=wait)
        if accepted is False:
            with self._persist_retry_lock:self._dirty_minutes.add(key)

    def _storage_error(self,action,args,kwargs,exc):
        self.report_probe_error('storage',type(exc).__name__)
        with self._persist_retry_lock:
            if action=='flush_minute':self._dirty_minutes.add(args[0])
            elif action=='save_raw_batch':self._recovery_raw.extend(args[0])
        logging.getLogger(__name__).error('历史存储降级：%s',type(exc).__name__)

    def _maybe_flush(self, ts):
        with self._persist_retry_lock:
            self._pending_raw=list(self._recovery_raw)+self._pending_raw
            self._recovery_raw.clear();retry=list(self._dirty_minutes);self._dirty_minutes.clear()
        self._pending_raw=self._pending_raw[-16000:]
        for key in retry:
            if key in self._minutes:self._flush_minute(key,wait=False)
        if self._pending_raw and self._writer.submit('save_raw_batch',(list(self._pending_raw),)):
            self._pending_raw.clear()

    def live_samples(self):
        return [item for values in self._live_by_target.values() for item in values]

    def replay(self, session_id, after=0, limit=5000):
        reset=bool(session_id and session_id!=self.session_id)
        if reset:after=0
        rows,more=self.db.replay(self.session_id,after,limit,time.time()-600)
        gaps=[]
        if rows and after and rows[0]['sequence']>after+1:gaps.append({'reason':'outside_retention_or_missing','after_sequence':after,'next_sequence':rows[0]['sequence']})
        return {'session_id':self.session_id,'reset':reset,'rows':rows,'has_more':more,
                'next_sequence':rows[-1]['sequence'] if rows else after,'last_sequence':self._sequence,
                'resolution':'raw','coverage_seconds':600,'gaps':gaps}

    # ---- 事件 ----
    def _on_event(self, ev):
        self.events.append(ev)
        self._writer.submit('add_event',(ev['ts'],ev['type'],ev['message']))
        level = {"offline": "critical", "latency_spike": "warning",
                 "recover": "info"}.get(ev["type"], "info")
        self._broadcast({"ts": ev["ts"], "type": "alert", "etype": ev["type"],
                         "level": level, "message": ev["message"], "notify":ev.get('notify',True)})

    # ---- 对外查询 ----
    def report_probe_error(self, protocol, code):
        self.probe_health.record_error(protocol, code, time.time())

    def overview(self):
        with self._lock:
            buffer = self.live_samples()
            traces = {k: list(v) for k, v in self.traces.items()}
            targets = list(self.targets)
            status = self._last_status
            uptime = time.monotonic() - self._start_mono
            latest_by_target = {kind: {key: dict(item) for key, item in values.items()}
                                for kind, values in self._latest_by_target.items()}
        latest = {}
        for item in reversed(buffer):
            if item["type"] in latest:
                continue
            latest[item["type"]] = item
            if len(latest) >= 4:
                break
        health = self.probe_health.snapshot(time.time())
        storage=self._writer.snapshot()
        try:summary=self.compute_summary(hours=1)
        except (OSError,TimeoutError):summary=quality_summary({});storage['error']='read_delayed'
        if storage['error'] or storage['write_age']>10:status='unknown' 
        if health['ping']['status'] != 'running':
            status = 'unknown'
        return {
            "storage": storage,
            "broadcast": {'queued':self.outbox.qsize(),'dropped':self.outbox_dropped},
            "quality_thresholds": thresholds(),
            "configuration_error": self.configuration_error,
            "config_revision": self.config_revision,
            "probe_revision": self._probe_revision,
            "network_context": self.network_context,
            "probe_health": health,
            "status": status,
            "session_id": self.session_id,
            "time_epoch": self.time_epoch,
            "last_sequence": self._sequence,
            "as_of": time.time(),
            "expected_interval": config.get('ping_interval'),
            "stat_window": config.get('stat_window'),
            "targets": targets,
            "traces": traces,
            "trace_enabled": config.get("enable_traceroute"),
            "latest": latest,
            "latest_by_target": latest_by_target,
            "uptime": uptime,
            "db_size": self.db.size_mb(),
            "summary": summary,
            "target_summaries": self.compute_target_summaries(hours=1, buffer=buffer, targets=targets),
        }

    def close(self):
        with self._lock:
            if self._closed:return self._writer.closed.is_set()
            self._closed=True
            self._maybe_flush(time.time())
            if self._minute_key is not None:
                self._flush_minute(self._minute_key,wait=False)
                self._writer.submit('aggregate_hour',(self._minute_key//3600*3600,))
                self._writer.submit('cleanup',(time.time(),))
        completed=self._writer.close(timeout=5)
        if not completed:logging.getLogger(__name__).error('存储尚未完成，待写数据不能保证落盘')
        return completed
