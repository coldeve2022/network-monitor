"""全局配置：静态常量 + 运行时设置（前端可改、实时生效）。"""
import sys
import json
import math
import logging
import threading
from pathlib import Path


def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
DB_PATH = APP_DIR / "data" / "metrics.db"
TARGETS_FILE = APP_DIR / "data" / "targets.json"   # 用户自定义 ping 目标持久化
PROBES_FILE = APP_DIR / "data" / "probes.json"
TRAFFIC_INTERFACE = ""
PROBE_REVISION = 0
SETTINGS_FILE = APP_DIR / "data" / "settings.json"

# ---------- 服务 ----------
HOST = "127.0.0.1"
WEB_PORT = 8768

# ---------- Ping 基础目标（始终监测）----------
# host 为 "auto" 时启动自动替换为默认网关
PING_TARGETS = [
    {"name": "网关", "host": "auto"},
    {"name": "百度", "host": "baidu.com"},
    {"name": "阿里DNS", "host": "223.5.5.5"},
    {"name": "腾讯DNS", "host": "119.29.29.29"},
]

# ---------- Traceroute 目标（PingPlotter 核心：对路径每一跳持续监测）----------
TRACE_TARGETS = ["223.5.5.5"]   # 对这些目标做 UDP traceroute，路径跳动态加入监测
TRACE_INTERVAL = 300            # 每 N 秒重新 trace 一次（路径可能变化）
TRACE_MAX_HOPS = 24             # 最多跳数
TRACE_TIMEOUT = 1.0             # 每跳等待超时，秒（运营商路由多不响应，1s 足够）
TRACE_IDLE_STOP = 3             # 连续 N 跳无响应即提前结束

# ---------- TCP 握手探测 ----------
TCP_TARGETS = [
    {"name": "百度443", "host": "baidu.com", "port": 443},
    {"name": "腾讯443", "host": "qq.com", "port": 443},
    {"name": "阿里443", "host": "aliyun.com", "port": 443},
]
TCP_TIMEOUT = 3.0

# ---------- DNS 解析探测 ----------
DNS_SERVERS = ["114.114.114.114", "223.5.5.5", "8.8.8.8"]
DNS_QUERY_HOST = "www.baidu.com"
DNS_TIMEOUT = 3.0

# ---------- 健康度阈值（静态）----------
EVAL_LATENCY_MED = 120
EVAL_LATENCY_BAD = 300
EVAL_LOSS_MED = 3
EVAL_LOSS_BAD = 10
OFFLINE_ROUNDS = 3

# ---------- 存储保留 ----------
MINUTE_RETENTION_HOURS = 24
HOUR_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 7
BUFFER_MAXLEN = 2400        # 内存保留的原始秒级点数量（约 20 分钟 @ 高频多目标）

# ---------- 运行时设置（前端 /api/config 可改，探针实时生效）----------
_RUNTIME = {
    "ping_interval": 1.0,      # 采样频率，秒（PingPlotter 官方最低到 1s）
    "ping_timeout": 2.0,       # 单包超时，秒
    "stat_window": 60,         # 统计窗口（轮），当前/平均/最小/最大/标准差/丢包 基于该窗口
    "tcp_interval": 5.0,       # TCP 探测间隔，秒
    "dns_interval": 10.0,      # DNS 探测间隔，秒
    "traffic_interval": 1.0,   # 流量采样间隔，秒
    "alert_latency_spike": 200,   # 延迟突增告警阈值，ms
    "alert_cooldown": 30,         # 同类告警冷却，秒
    "trace_interval": 300,        # 重新 traceroute 间隔，秒
    "ai_daily_limit": 20,
    "ai_min_interval": 10,
    "enable_traceroute": False,   # 默认关闭路径追踪（PingPlotter 逐跳监测）；开启后 TraceManager 自动加跳目标
}
DEFAULT_RUNTIME = dict(_RUNTIME)
_RUNTIME_LOCK = threading.RLock()
_SETTINGS_WRITE_LOCK = threading.Lock()
_PROBE_WRITE_LOCK = threading.Lock()

# 前端设置面板的可调项与范围（用于校验与提示）
SETTING_SCHEMA = {
    "ping_interval": {"label": "Ping 采样频率", "unit": "秒", "min": 0.5, "max": 60, "step": 0.5},
    "ping_timeout": {"label": "Ping 超时", "unit": "秒", "min": 0.5, "max": 10, "step": 0.5},
    "stat_window": {"label": "统计窗口", "unit": "轮", "min": 5, "max": 600, "step": 5},
    "tcp_interval": {"label": "TCP 探测间隔", "unit": "秒", "min": 1, "max": 120, "step": 1},
    "dns_interval": {"label": "DNS 探测间隔", "unit": "秒", "min": 2, "max": 120, "step": 1},
    "traffic_interval": {"label": "流量采样间隔", "unit": "秒", "min": 0.5, "max": 30, "step": 0.5},
    "alert_latency_spike": {"label": "延迟突增告警", "unit": "ms", "min": 20, "max": 1000, "step": 10},
    "alert_cooldown": {"label": "告警冷却", "unit": "秒", "min": 5, "max": 600, "step": 5},
    "trace_interval": {"label": "路径重测间隔", "unit": "秒", "min": 30, "max": 3600, "step": 30},
    "ai_daily_limit": {"label":"AI每日调用上限","unit":"次","min":1,"max":1000,"step":1},
    "ai_min_interval": {"label":"AI调用最小间隔","unit":"秒","min":5,"max":3600,"step":5},
    "enable_traceroute": {"label": "启用路径追踪", "type": "bool"},
}


def get(key, default=None):
    with _RUNTIME_LOCK:
        return _RUNTIME.get(key, default)


def _validate(values):
    """Validate the entire batch before changing any setting."""
    result = {}
    for k, v in values.items():
        if k not in SETTING_SCHEMA:
            raise ValueError(f"未知设置：{k}")
        schema = SETTING_SCHEMA[k]
        if schema.get("type") == "bool":
            if isinstance(v, str):
                value = v.strip().lower()
                if value not in ("0", "1", "false", "true", "no", "yes", "off", "on"):
                    raise ValueError(f"{schema['label']}必须为开关值")
                v = value in ("1", "true", "yes", "on")
            elif not isinstance(v, (bool, int)) or v not in (0, 1):
                raise ValueError(f"{schema['label']}必须为开关值")
            result[k] = bool(v)
        else:
            try:
                if isinstance(v, bool):
                    raise ValueError()
                v = float(v)
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"{schema['label']}必须为数字") from None
            if not math.isfinite(v) or not schema['min'] <= v <= schema['max']:
                raise ValueError(f"{schema['label']}范围：{schema['min']}–{schema['max']}")
            if k == 'stat_window' and not v.is_integer():
                raise ValueError("统计窗口必须为整数")
            result[k] = v
    return result


def set_many(values):
    """Persist atomically; failed writes leave the running settings unchanged."""
    validated = _validate(values)
    with _SETTINGS_WRITE_LOCK:
        with _RUNTIME_LOCK:updated = {**_RUNTIME, **validated}
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(SETTINGS_FILE)
        finally:
            temporary.unlink(missing_ok=True)
        with _RUNTIME_LOCK:_RUNTIME.update(validated)


def snapshot():
    with _RUNTIME_LOCK:
        return dict(_RUNTIME)


def load_settings():
    if not SETTINGS_FILE.exists():
        return
    try:
        values = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        validated = _validate(values)
        with _RUNTIME_LOCK:
            _RUNTIME.update(validated)
    except (OSError, ValueError, TypeError, AttributeError):
        logging.getLogger(__name__).warning('设置文件无法读取，使用默认设置', exc_info=True)


load_settings()


def probe_snapshot():
    with _RUNTIME_LOCK:
        return json.loads(json.dumps({'tcp_targets':TCP_TARGETS,'dns_servers':DNS_SERVERS,
            'dns_query_host':DNS_QUERY_HOST,'trace_targets':TRACE_TARGETS,'traffic_interface':TRAFFIC_INTERFACE}))

def set_probe_config(values, persist=True):
    global TCP_TARGETS,DNS_SERVERS,DNS_QUERY_HOST,TRACE_TARGETS,TRAFFIC_INTERFACE,PROBE_REVISION
    from core.probe_config import validate
    with _PROBE_WRITE_LOCK:
        previous=probe_snapshot()
        validated=validate(values,previous)
        if persist:
            PROBES_FILE.parent.mkdir(parents=True,exist_ok=True)
            temporary=PROBES_FILE.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(validated,ensure_ascii=False,indent=2),encoding='utf-8')
                temporary.replace(PROBES_FILE)
            finally:temporary.unlink(missing_ok=True)
        with _RUNTIME_LOCK:
            TCP_TARGETS=validated['tcp_targets'];DNS_SERVERS=validated['dns_servers']
            DNS_QUERY_HOST=validated['dns_query_host'];TRACE_TARGETS=validated['trace_targets']
            TRAFFIC_INTERFACE=validated['traffic_interface']
            if validated!=previous:PROBE_REVISION+=1

def load_probe_settings():
    if not PROBES_FILE.exists():return
    try:set_probe_config(json.loads(PROBES_FILE.read_text(encoding='utf-8')),persist=False)
    except (ValueError,TypeError,OSError):logging.getLogger(__name__).warning('专项检测配置读取失败，原文件已保留')

def protocol_snapshot():
    with _RUNTIME_LOCK:return {'revision':PROBE_REVISION,'settings':probe_snapshot()}
