# NetworkMonitor 网络质量监测

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#)

Windows 本地网络质量监测工具，PingPlotter 风格的持续采样与可视化，技术栈为 Python / FastAPI / SQLite / ECharts。

解决的问题是「网络到底哪一段在抖」：对多个目标持续 ping、对路径逐跳采样，同时记录 TCP 握手、DNS 解析和网卡流量，把延迟、丢包、抖动摆到同一条时间轴上对照，而不是只给一个平均延迟数字。

## 快速开始

两种用法。

**下载免安装版**：到 [Releases](../../releases) 下载 `NetworkMonitor.exe`，放到任意目录双击运行，浏览器自动打开 `http://127.0.0.1:8768`。

**从源码运行**：

```bat
git clone https://github.com/coldeve2022/network-monitor.git
cd network-monitor
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

需要 Python 3.10 及以上、Windows 10/11。程序只监听 `127.0.0.1`，不开放远程访问。关闭浏览器不停止采集，控制台 Ctrl+C 才会停止探针并落盘。

## 功能

- Ping目标与可选路径跳持续监测；真实超时、未调度、解析错误和过期样本分开表示。
- 主图延迟/丢包/抖动/标准差/CV/MOS估算；右端跟随保留用户跨度，浏览历史保持范围；缺采集区间单独说明。
- TCP连接、DNS解析与网卡流量；专项目标和网卡可以在设置中编辑。网卡速率是当前流量，不是带宽测速。
- 历史1/6/24小时、7/30天；Ping/TCP/DNS/流量CSV导出。24小时内为分钟聚合，更早为小时聚合。
- 本地诊断无需Key。可预览准备发送的监测摘要，再显式生成DashScope AI分析；后台不自动调用付费接口。
- 主题、密度、字号、主图高度、表格位置与可拖动分隔条、列显隐/顺序、目标顺序/颜色、界面方案导入导出。
- 采样、超时、统计窗口、告警和路径开关持久化。目标更改、专项检测和Key分别保存，设置界面注明作用范围。

ICMP无响应不能单独证明业务断网；应结合实际业务的TCP/DNS结果。MOS是经验公式估算，不能当作实际通话测评。TCP耗时包含系统解析与连接，不等于纯网络往返延迟。

## 数据

源码运行使用项目 `data/`，发布运行使用exe同级 `data/`：

- `metrics.db`：分钟、小时、原始样本、事件和AI调用计数。
- `targets.json`：Ping目标身份与配置版本。
- `settings.json`：采样与告警设置。
- `probes.json`：TCP、DNS、路径与流量网卡设置。
- `ai_key.json`：可选本地AI Key；环境变量 `DASHSCOPE_API_KEY` 也有效。不要分享此文件。

原始样本保留最近60分钟，另有25万条总上限；重连续传当前会话最近10分钟。更早只显示聚合，不能恢复已清理的秒级细节。分钟保留24小时，小时30天，事件7天。边界小时的充分统计量最多额外保留不足1小时，以安全处理迟到样本。CSV只导出已保存的聚合，不包含当前未完成分钟；AI摘要会另行合并当前分钟。

升级前自动创建 `metrics.pre-v2-*.db` 一致性备份，再新增表；原有表保留。旧数据缺样本计数时明确标近似，已经写错或丢失的旧历史不能反推修复。回退前先退出新版，使用部署前备份恢复程序与数据。

## 运行参数

参数均可省略：

```bat
NetworkMonitor.exe --no-browser
NetworkMonitor.exe --data-dir "D:\monitor-session" --port 18883 --duration 60 --no-browser
```

`--data-dir`使用独立数据，`--port`修改本机端口，`--duration`限时采集后正常落盘退出，0表示持续运行。只监听loopback，不提供远程访问。

## 开发与验证

代码要求Python3.10+，本机验证环境为Python3.13。使用锁定依赖，不因一次修复升级整个技术栈。

```bat
python -m pip install -r requirements-lock.txt
python -B -m unittest discover -s tests -v
python -B test_run.py
node tests/frontend_regression.cjs
node tests/runtime_contract.cjs
node tests/chart_contract_browser.cjs
node tests/ui_accessibility_browser.cjs
```

浏览器测试需要Playwright1.62.1与Chrome；可设置 `PLAYWRIGHT_MODULE` 和 `CHROME_PATH` 指定已安装位置。Node只用于开发测试。完整API/线程回归需要正常Windows本地套接字权限，受限沙箱可能在系统事件循环自管道初始化时阻塞。

`test_run.py`默认只用临时数据和合成测量，不改用户目标、设置或Key，不调用真实探测/AI。`tests/serve_fixture.py`同样隔离全部可写数据路径。

## 构建与接口

`build.bat`保留 `NetworkMonitor.spec` 和 `dist/data`，不会先清空发布目录。重新生成图标另需 `requirements-build.txt` 中的Pillow。程序运行时应先退出再替换exe。

`/api/version`提供版本、schema和构建指纹。API写入及WebSocket需要首页生成的本次运行会话Token，浏览器自动处理；外部脚本必须显式携带。普通HTTP读取仍限本地Host/Origin。

本轮变化见 `docs/repair-2026-10-28.md`；2026-10-26修复记录保留作历史材料，不作为新版验收结论。

真实连续72小时、跨Windows权限/驱动、VPN/休眠恢复和付费AI账户须单独实机验收。主动带宽测速、逐进程流量、无线信道扫描和远程多机仍是独立扩展项目。

## 项目结构

```
network-monitor/
├── main.py              入口，命令行参数解析
├── config.py            静态常量与运行时设置
├── version.py           版本号与schema标识
├── core/                采集与分析
│   ├── collector.py     采样调度与落盘
│   ├── ping_probe.py    ICMP探测
│   ├── tcp_probe.py     TCP握手
│   ├── dns_probe.py     DNS解析
│   ├── traceroute.py    路径追踪
│   ├── traffic_probe.py 网卡流量
│   ├── evaluator.py     质量判定
│   └── statistics.py    聚合与统计
├── storage/             SQLite读写、迁移、有界写者
├── web/                 FastAPI服务与前端
│   ├── server.py
│   └── static/          页面、图表、样式
├── tests/               单元与浏览器契约测试
├── docs/                修复与优化记录
└── build.bat            PyInstaller打包脚本
```

## 参与与反馈

发现问题请开 [Issue](../../issues)，附上版本号（`/api/version` 可查）、Windows 版本和复现步骤。判断网络问题前先排除 ICMP 被目标侧限速或屏蔽的情况，单看 ping 超时不能直接当成断网。

## 开源协议

[MIT](LICENSE)。

