"""入口：启动采集探针线程 + Web 服务，自动打开浏览器。

源码运行：python main.py
打包运行：build.bat 打出 NetworkMonitor.exe 后双击。
"""
import socket
import argparse
from pathlib import Path
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

import config
from core.collector import Collector
from core.dns_probe import DnsProbe
from core.ping_probe import PingProbe
from core.tcp_probe import TcpProbe
from core.trace_manager import TraceManager
from core.traffic_probe import TrafficProbe
from web.server import create_app


def detect_gateway():
    from core.network_context import detect_gateway as read_gateway
    return read_gateway()


def _port_in_use(host, port):
    """启动前快速检查端口，避免 uvicorn 直接抛栈给用户。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
        return False


def main(argv=None):
    parser=argparse.ArgumentParser(description='本地网络质量监测')
    parser.add_argument('--data-dir',type=Path,help='独立监测数据目录')
    parser.add_argument('--port',type=int,help='本机Web端口')
    parser.add_argument('--no-browser',action='store_true',help='保持后台采集，不自动打开浏览器')
    parser.add_argument('--duration',type=float,default=0,help='限时监测秒数，0表示持续运行')
    args=parser.parse_args([] if argv is None else argv)
    if args.port is not None:
        if not 1<=args.port<=65535:parser.error('端口范围1—65535')
        config.WEB_PORT=args.port
    if args.data_dir:
        data=args.data_dir.resolve();data.mkdir(parents=True,exist_ok=True)
        config.DB_PATH=data/'metrics.db';config.TARGETS_FILE=data/'targets.json';config.SETTINGS_FILE=data/'settings.json';config.PROBES_FILE=data/'probes.json'
        import web.server as web_server
        web_server.AI_KEY_FILE=data/'ai_key.json'
        with config._RUNTIME_LOCK:
            config._RUNTIME.clear();config._RUNTIME.update(config.DEFAULT_RUNTIME)
        config.load_settings()
    if args.duration<0:parser.error('监测时长不能为负数')
    if _port_in_use(config.HOST, config.WEB_PORT):
        print(f"[error] 端口 {config.WEB_PORT} 已被占用，请先关闭占用进程后重试。")
        return 1

    config.load_probe_settings()
    collector = Collector()
    started = []
    browser_timer = None
    try:
        gateway = detect_gateway()
        collector.set_gateway(gateway)
        if gateway:
            print(f"[init] 默认网关: {gateway}")
        else:
            print("[init] 未探测到默认网关，跳过网关目标")
    
        probes = [
            PingProbe(collector.on_result, collector.ping_targets),
            TcpProbe(collector.on_result),
            DnsProbe(collector.on_result),
            TrafficProbe(collector.on_result),
        ]
        for p in probes:
            p.start()
            started.append(p)
    
        trace_mgr = TraceManager(collector)
        trace_mgr.start()
        started.append(trace_mgr)
    
        url = f"http://{config.HOST}:{config.WEB_PORT}"
        print(f"[init] 探针已启动，仪表盘: {url}")
        print(f"[init] 追踪目标: {config.TRACE_TARGETS}")
        print("[init] 按 Ctrl+C 退出")
    
        app = create_app(collector)
        if not args.no_browser:
            browser_timer = threading.Timer(1.2, lambda: webbrowser.open(url))
            browser_timer.daemon = True
            browser_timer.start()
        if args.duration:
            server=uvicorn.Server(uvicorn.Config(app,host=config.HOST,port=config.WEB_PORT,log_level='warning',timeout_graceful_shutdown=5))
            deadline=threading.Timer(args.duration,lambda:setattr(server,'should_exit',True));deadline.daemon=True;deadline.start()
            try:server.run()
            finally:deadline.cancel()
        else:uvicorn.run(app, host=config.HOST, port=config.WEB_PORT, log_level="warning",timeout_graceful_shutdown=5)
    except KeyboardInterrupt:
        pass
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        for p in started:
            p.stop()
        deadline=time.monotonic()+5
        for p in started:
            p.join(timeout=max(0,deadline-time.monotonic()))
        collector.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
