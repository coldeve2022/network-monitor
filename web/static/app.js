(() => {
  "use strict";

  const PALETTE = ["#4da3ff", "#a78bfa", "#22d3ee", "#34d399", "#fbbf24", "#f472b6", "#fb923c", "#94a3b8"];
  const REAL_KEEP = 1200;                    // 实时秒级点保留 10 分钟
  const DEFAULT_VIEW_MS = 10 * 60 * 1000;   // 默认展示最近 10 分钟
  const STATUS = { good: "优", fair: "良", bad: "差", offline: "ICMP均无响应", unknown: "未知" };

  const THEMES = {
    dark:  { axis: "#283450", split: "#1c2638", label: "#8b98ab", tipBg: "#1c2638", tipBorder: "#283450", tipText: "#e6edf3" },
    light: { axis: "#d8dfeb", split: "#e8edf5", label: "#5b6b84", tipBg: "#ffffff", tipBorder: "#d8dfeb", tipText: "#1a2332" },
  };
  function storageGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  let theme = storageGet("nm-theme")
    || (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  if (!THEMES[theme]) theme = "dark";
  document.documentElement.dataset.theme = theme;
  const t = () => THEMES[theme];
  const chartFontSize = (base) => Math.round(base * (parseFloat(document.documentElement.style.getPropertyValue('--ui-font-size')) || 16) / 16);
  const accentColor = () => document.documentElement.style.getPropertyValue('--accent').trim() || '#4da3ff';

  const targetKey = (x) => JSON.stringify([x.host, x.kind || "base", x.trace_of || "", x.hop ?? ""]);
  const targetLabel = (x) => targets.filter((tgt) => tgt.name === x.name).length > 1
    ? x.name + " · " + x.host + (x.kind === "hop" ? " · 跳" + x.hop : "") : x.name;
  const $ = (id) => document.getElementById(id);
  const api = window.NMApi.createClient({fetch:(...args)=>fetch(...args),getToken:()=>document.querySelector('meta[name="nm-session-token"]')?.content||''});
  const snapshotGate = new window.NMState.SnapshotGate();
  const apiFetch = (url,options) => api.request(url,options);
  const readJSON = (url,options) => api.json(url,options);
  let overviewRequest = null;
  function fetchOverview() {
    if (overviewRequest) return overviewRequest;
    const ticket = snapshotGate.begin();
    overviewRequest = readJSON('/api/overview').then(snapshot => snapshotGate.accept(snapshot,ticket) ? snapshot : null).finally(()=>{overviewRequest=null;});
    return overviewRequest;
  }
  const escapeHtml = (s) => String(s == null ? "" : s)
    .replace(/[&<>"']/g, (c) => (
      {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]
    ));
  const charts = {
    latency: echarts.init($("chartLatency")),
    loss: echarts.init($("chartLoss")),
    jitter: echarts.init($("chartJitter")),
    traffic: echarts.init($("chartTraffic")),
    tcp: echarts.init($("chartTcp")),
    dns: echarts.init($("chartDns")),
    histLat: echarts.init($("chartHistLat")),
    histLoss: echarts.init($("chartHistLoss")),
  };

  // ---------- DPI 自适应：按显示器分辨率/系统缩放动态设置 rem 基准字号 ----------
  function applyDpi() {
    const preferred = document.documentElement.style.getPropertyValue('--ui-font-size');
    if (preferred) { document.documentElement.style.fontSize = preferred; return; }
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const w = window.innerWidth;
    let base = 18;                       // 16 → 18，整体更醒目
    if (dpr >= 2) base = 21;            // 200% 缩放（高分屏）
    else if (dpr >= 1.4) base = 20;     // 150% 缩放
    else if (dpr >= 1.2) base = 19;     // 125% 缩放
    if (w <= 1100) base -= 1.5;         // 窄窗口略缩小
    else if (w >= 1920) base += 0.5;    // 大屏放大
    document.documentElement.style.fontSize = base + "px";
  }

  function onResize() {
    applyDpi();
    Object.values(charts).forEach((c) => { try { c.resize(); } catch (e) { /* 忽略 */ } });
  }
  window.addEventListener("resize", onResize);
  applyDpi();

  const baseAxis = () => ({
    type: "time",
    axisLine: { lineStyle: { color: t().axis } },
    axisLabel: { color: t().label, fontSize: chartFontSize(12), hideOverlap: true,
      formatter: (v) => new Date(v).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" }) },
    splitLine: { show: false },
  });
  const baseGrid = { left: 48, right: 16, top: 30, bottom: 60 };
  const tip = () => ({
    backgroundColor: t().tipBg, borderColor: t().tipBorder,
    textStyle: { color: t().tipText, fontSize: chartFontSize(13) },
  });
  // scale: true 让 ECharts 自动适配数据范围（不强制从 0 开始），避免小数值贴底看不见
  const yValue = (name) => ({
    type: "value", name, scale: true,
    nameTextStyle: { color: t().label },
    axisLabel: { color: t().label, fontSize: chartFontSize(12) },
    splitLine: { lineStyle: { color: t().split } },
    splitNumber: 4,
  });

  // ---------- 数据 ----------
  const ping = {};      // host -> {t, v, loss, jitter, lossT}
  const hist = {};      // host -> {t, v}
  const mosSeries = {}; // host -> {t, v}  MOS 语音质量（1-5），每 30s 聚合一次
  const cvSeries = {};  // host -> {t, v}  CV 变异系数（%），每 30s 聚合一次
  const traffic = { t: [], rx: [], tx: [] };
  const tcpData = {};   // name -> {t, v}
  const dnsData = {};   // server -> {t, v}
  let targets = [];
  let traces = {};
  let selectedHost = null;
  let follow = true;
  let viewSpanMs = DEFAULT_VIEW_MS;
  let manualWindow = null;
  const resolveViewWindow = window.NMCharts.resolveViewWindow;
  function latestTime(data) {
    let latest = null;
    for (const series of data) for (const pair of series) {
      if (Number.isFinite(pair[0]) && (latest == null || pair[0] > latest)) latest = pair[0];
    }
    return latest;
  }
  let mainMetric = "latency";        // 主图指标：latency/loss/jitter/std/cv/mos
  // 纵轴范围：log=对数轴（默认，8ms 和 2000ms 能同屏看清）；typical=按 p95；extreme=装下全部极值
  // 峰值曲线只在"聚焦某个目标"时画：9 条一起铺开会糊成一片，反而看不懂
  let axisMode = storageGet("nm-axismode") || "log";
  if (!["log", "typical", "extreme"].includes(axisMode)) axisMode = "log";
  const colorOf = {};
  const cur = {};           // 状态灯/DNS 探测缓存
  let lastChartTs = 0;

  // ---------- 展示指标（可勾选，localStorage 记忆） ----------
  const METRICS = ["latency", "loss", "jitter", "traffic", "tcp", "dns", "histLat", "histLoss"];
  const METRIC_LABELS = {
    latency: "目标与主图", loss: "丢包率", jitter: "抖动", traffic: "网卡速率",
    tcp: "TCP 握手", dns: "DNS 解析", histLat: "历史延迟", histLoss: "历史丢包",
  };
  let visibleMetrics = loadMetrics();

  function loadMetrics() {
    try {
      const s = localStorage.getItem("nm-metrics");
      if (s == null) return METRICS.slice();
      const arr = s.split(",");
      return METRICS.filter((m) => arr.includes(m));
    } catch (e) { return METRICS.slice(); }
  }
  function saveMetrics() {
    try { localStorage.setItem("nm-metrics", visibleMetrics.join(",")); } catch (e) { /* 忽略 */ }
  }
  function metricVisible(m) { return visibleMetrics.includes(m); }

  function renderMetricSelector() {
    const bar = $("metricBar");
    bar.innerHTML = '<span class="mb-title">展示指标：</span>';
    for (const m of METRICS) {
      const lab = document.createElement("label");
      lab.className = "metric-chip" + (metricVisible(m) ? " on" : "");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = metricVisible(m);
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(METRIC_LABELS[m]));
      cb.addEventListener("change", () => toggleMetric(m));
      bar.appendChild(lab);
    }
  }

  // ---------- 声音开关 ----------
  let soundOn = storageGet("nm-sound") !== "off";
  function setSound(on) {
    soundOn = !!on;
    try { localStorage.setItem("nm-sound", soundOn ? "on" : "off"); } catch (e) { /* 忽略 */ }
    const btn = $("btnSound");
    if (btn) btn.textContent = soundOn ? "🔔" : "🔕";
  }

  function toggleMetric(m) {
    if (metricVisible(m)) visibleMetrics = visibleMetrics.filter((x) => x !== m);
    else visibleMetrics.push(m);
    saveMetrics();
    renderMetricSelector();
    applyMetricVisibility();
    requestAnimationFrame(() => { Object.values(charts).forEach((chart) => chart.resize()); });
    lastChartTs = 0;
    maybeUpdateCharts();
  }

  function applyMetricVisibility() {
    for (const m of METRICS) {
      const panel = $("panel" + m[0].toUpperCase() + m.slice(1));
      if (panel) panel.classList.toggle("hidden", !metricVisible(m));
    }
  }

  function push(arr, v, cap) { arr.push(v); if (arr.length > cap) arr.shift(); }
  function fmtMs(ms) {
    if (ms == null) return "--";
    return ms >= 1000 ? (ms / 1000).toFixed(2) + " s" : Math.round(ms) + " ms";
  }
  function fmtBps(b) {
    if (!b) return "0 B/s";
    if (b >= 1e6) return (b / 1e6).toFixed(2) + " MB/s";
    if (b >= 1e3) return (b / 1e3).toFixed(1) + " KB/s";
    return Math.round(b) + " B/s";
  }
  function mean(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null; }
  function fmtClock(ts) { return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false, month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit" }); }
  function fmtUptime(s) {
    s = Math.floor(s);
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
          m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d) return `${d} 天 ${h} 小时`;
    if (h) return `${h} 小时 ${m} 分`;
    if (m) return `${m} 分 ${sec} 秒`;
    return `${sec} 秒`;
  }
  const LEVEL = { offline: "critical", latency_spike: "warning", recover: "info" };

  // ---------- 目标与路径 ----------
  function setTargets(list) {
    const previous=new Map(targets.filter(t=>t.kind==='base').map(t=>[t.host,t.target_id]));
    for(const target of list)if(target.kind==='base'&&previous.has(target.host)&&previous.get(target.host)!==target.target_id){delete ping[target.host];delete cvSeries[target.host];delete mosSeries[target.host];}
    targets = list;
    if (selectedHost && !targets.some((x) => targetKey(x) === selectedHost)) selectedHost = null;
    const active = new Set(targets.map((x) => x.host));
    for (const map of [ping, hist, mosSeries, cvSeries]) {
      for (const host of Object.keys(map)) if (!active.has(host)) delete map[host];
    }
    list.forEach((x, i) => { if (!(x.host in colorOf)) colorOf[x.host] = cur.displayPrefs?.targetColors?.[x.host] || PALETTE[i % PALETTE.length]; });
    renderTargetCards();
    renderTable();
    applyFocus();
    for (const host of Object.keys(ping)) {
      const d = ping[host];
      if (d.last) { updateTargetCard(d.last); updateTableRow(d.last); }
    }
    refreshTableSummaries();
    if (!$("settingsModal").classList.contains("hidden")) renderTargetList();
    updateChart();
    updateBars();
  }

  function renderPath() {
    const parts = [];
    for (const [tgt, hops] of Object.entries(traces)) {
      const chain = hops.map((h) => h ? escapeHtml(h) : '<span class="star">*</span>').join(" → ");
      parts.push(`<b>${escapeHtml(tgt)}</b>: ${chain}`);
    }
    $("pathInfo").innerHTML = parts.length ? parts.join(" &nbsp;|&nbsp; ") :
      (cur.traceEnabled ? "等待路径结果（路由器可能不响应）" : "路径追踪已关闭 · 可在设置中启用");
  }

  function renderTargetCards() {
    const wrap = $("targetCards");
    wrap.innerHTML = "";
    for (const tgt of targets) {
      const card = document.createElement("div");
      card.className = "tcard" + (tgt.kind === "hop" ? " hop" : "");
      card.dataset.host = tgt.host;
      card.dataset.targetKey = targetKey(tgt);
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.innerHTML =
        `<div class="tc-head"><span class="tc-dot off"></span><span class="tc-name">${escapeHtml(tgt.name)}</span></div>` +
        `<div class="tc-host">${escapeHtml(tgt.host)}</div>` +
        `<div class="tc-lat">--</div>` +
        `<div class="tc-meta"><span class="tc-loss">丢包 --</span> · <span class="tc-jit">抖动 --</span></div>`;
      card.addEventListener("click", () => {
        selectedHost = (selectedHost === targetKey(tgt)) ? null : targetKey(tgt);
        applyFocus();
        updateChart();
      });
      card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); } });
      wrap.appendChild(card);
    }
  }

  function applyFocus() {
    document.querySelectorAll("#targetCards .tcard").forEach((c) => {
      c.classList.toggle("focused", c.dataset.targetKey === selectedHost);
    });
    document.querySelectorAll("#targetTable tbody tr").forEach((r) => {
      r.classList.toggle("focused", r.dataset.targetKey === selectedHost);
    });
  }

  function updateTargetCard(item) {
    document.querySelectorAll(`#targetCards .tcard[data-host="${CSS.escape(item.host)}"]`).forEach((card) => {
    const lat = card.querySelector(".tc-lat");
    const isLost = item.avg == null;
    const unmeasured = ["skipped","local_error","resolution_error","cancelled"].includes(item.state);
    lat.textContent = unmeasured ? "未采样" : isLost ? "×" : Math.round(item.avg) + " ms";
    lat.classList.toggle("small", !isLost && item.avg >= 100);
    const dot = card.querySelector(".tc-dot");
    dot.className = "tc-dot " + (isLost ? "bad" : (item.avg > (cur.thresholds?.latency_bad || 300) ? "bad" : ""));
    card.querySelector(".tc-loss").textContent = "丢包 " + item.loss + "%";
    card.querySelector(".tc-loss").className = "tc-loss" + (item.loss > 3 ? " loss-bad" : "");
    card.querySelector(".tc-jit").textContent = "抖动 " + (item.jitter == null ? "--" : Math.round(item.jitter) + " ms");
    });
  }

  function renderTable() {
    const tb = document.querySelector("#targetTable tbody");
    tb.innerHTML = "";
    for (const tgt of targets) {
      const tr = document.createElement("tr");
      tr.dataset.host = tgt.host;
      tr.dataset.targetKey = targetKey(tgt);
      tr.tabIndex = 0;
      if (tgt.kind === "hop") tr.classList.add("hop");
      tr.innerHTML =
        `<td><div>${escapeHtml(tgt.name)}</div><div class="host-cell">${escapeHtml(tgt.host)}</div></td>` +
        `<td class="v-cur">--</td><td class="v-avg">--</td>` +
        `<td class="v-mos">--</td><td class="v-cv">--</td>` +
        `<td class="v-loss">--</td><td class="v-jit">--</td><td class="v-avail">--</td>`;
      tr.addEventListener("click", () => {
        selectedHost = (selectedHost === targetKey(tgt)) ? null : targetKey(tgt);
        applyFocus();
        updateChart();
      });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tr.click(); } });
      const columns={current:'v-cur',avg:'v-avg',mos:'v-mos',cv:'v-cv',loss:'v-loss',jitter:'v-jit',availability:'v-avail'};
      tr.querySelector('td').dataset.column='target';
      for (const [key,cls] of Object.entries(columns)) tr.querySelector('.'+cls).dataset.column=key;
      tr.querySelector('td').title=tgt.name+' · '+tgt.host;
      tb.appendChild(tr);
    }
    window.dispatchEvent(new Event('nm-targets-rendered'));
  }

  function updateTableRow(item) {
    document.querySelectorAll(`#targetTable tr[data-host="${CSS.escape(item.host)}"]`).forEach((tr) => {
    const set = (cls, txt, bad, warn) => {
      const td = tr.querySelector("." + cls);
      td.textContent = txt;
      td.className = cls + (bad ? " bad" : warn ? " warn" : "");
    };
    const current = item.avg;
    set("v-cur", ["skipped","local_error","resolution_error","cancelled"].includes(item.state) ? "未采样" : current == null ? "×" : Math.round(current), current != null && current > (cur.thresholds?.latency_bad || 300));
    renderSummaryCells(tr,(cur.target_summaries||{})[item.host]||{});
    set("v-loss", item.loss == null ? "--" : item.loss + "%", item.loss > 3);
    set("v-jit", item.jitter == null ? "--" : Math.round(item.jitter), item.jitter > 20);
    });
  }

  // ---------- 主图（延迟折线 + 丢包红点 + 时间缩放条） ----------
  // 通用数值格式化：避免出现超长小数（27.53500000000004 之类的）
  function fmtVal(v, unit) {
    if (v == null || !isFinite(v)) return "--";
    if (Math.abs(v) >= 100) return Math.round(v) + (unit || "");
    if (Math.abs(v) >= 10) return v.toFixed(1) + (unit || "");
    return v.toFixed(2) + (unit || "");
  }

  // 收集所有 ping 数值用于动态算 y 轴范围，确保线不被压扁
  // 所有数据的实际时间范围：ping buffer（最近 10min 秒级实时）+ hist（24h 分钟聚合）
  // → 让 slider 拖到任意 24h 内窗口都能正确算出 xStart/xEnd
  // 当前图表的 x 轴可视窗口（ms epoch）。
  // Prefer ECharts' calculated absolute range; fall back to percentages when absent.
  function currentXWindow(chart, now) {
    try {
      const opt = chart.getOption();
      const dz = (opt.dataZoom || []).find((d) => d.type === "slider");
      const times = (opt.series || []).flatMap((s) => (s.data || []).map((p) => p[0])).filter(Number.isFinite);
      const bMin = times.length ? times.reduce((a, b) => Math.min(a, b)) / 1000 : null;
      const bMax = times.length ? times.reduce((a, b) => Math.max(a, b)) / 1000 : null;
      if (dz && bMin != null && bMax != null && bMax > bMin) {
        const bMinMs = bMin * 1000, bMaxMs = bMax * 1000, span = bMaxMs - bMinMs;
        const startPct = (dz.startValue != null)
          ? Math.max(0, Math.min(1, (dz.startValue - bMinMs) / span))
          : Math.max(0, Math.min(1, (dz.start ?? 0) / 100));
        const endPct = (dz.endValue != null)
          ? Math.max(0, Math.min(1, (dz.endValue - bMinMs) / span))
          : Math.max(0, Math.min(1, (dz.end ?? 100) / 100));
        return { xStart: bMinMs + startPct * span, xEnd: bMinMs + endPct * span };
      }
      return { xStart: now - DEFAULT_VIEW_MS, xEnd: now };
    } catch (e) {
      return { xStart: now - DEFAULT_VIEW_MS, xEnd: now };
    }
  }

  // 统计 series 在 [xStart, xEnd] 窗口内的 y min/max。
  // seriesList 是嵌套数组：每个元素是一个 series（[[x, y], ...]）。
  //（之前的实现误把嵌套当扁平 [x,y] pair 迭代，导致 yMax=null 永远走 fallback）
  function yBoundsInWindow(seriesList, xStart, xEnd) {
    let yMin = null, yMax = null;
    if (!Array.isArray(seriesList)) return { yMin, yMax };
    for (const series of seriesList) {
      if (!Array.isArray(series)) continue;
      for (const pair of series) {
        if (!Array.isArray(pair) || pair.length < 2) continue;
        const x = pair[0], v = pair[1];
        if (typeof v !== "number" || !isFinite(v)) continue;
        if (xStart != null && x < xStart) continue;
        if (xEnd != null && x > xEnd) continue;
        if (yMin === null || v < yMin) yMin = v;
        if (yMax === null || v > yMax) yMax = v;
      }
    }
    return { yMin, yMax };
  }

  // 抗顶格 pad：max 至少比数据 max 多 max(数据 max*12%, 1)，min 同理。
  // 视觉上确保线/点不贴轴顶；min >= 0 时也留 5% 下空间。
  function applyYPad(yMin, yMax, opts = {}) {
    const { floorZero = true, minTop = 0.12, minFloor = 0.5 } = opts;
    if (yMax == null) return { min: floorZero ? 0 : null, max: 5 };
    let mn = yMin != null ? yMin : 0;
    let mx = yMax;
    const range = Math.max(mx - mn, 1e-6);
    const topPad = Math.max(mx * minTop, minFloor);
    const floorPad = Math.max(range * minTop, minFloor);
    let newMin = mn - floorPad;
    let newMax = mx + topPad;
    if (floorZero) newMin = Math.max(0, newMin);
    // 避免 min >= max 的退化
    if (newMax <= newMin) newMax = newMin + Math.max(1, topPad);
    return { min: newMin, max: newMax };
  }

  // 给带 dataZoom 的图表绑定联动：用户拖动 slider 时按当前可见窗口重算 y 轴，
  // 解决"全时段有突出峰值 → 缩放后其他数据变化不明显"。
  function bindZoomY(chart, collectData) {
    if (!chart) return;
    chart.on("datazoom", () => {
      try {
        const now = Date.now();
        const { xStart, xEnd } = currentXWindow(chart, now);
        const { yMin, yMax } = yBoundsInWindow(collectData(), xStart, xEnd);
        let actualYMin = yMin, actualYMax = yMax;
        // 窗口无数据时 fallback 用全数据 yMax，避免 y 轴过小
        if (actualYMax == null) {
          ({ yMin: actualYMin, yMax: actualYMax } = yBoundsInWindow(collectData(), null, null));
        }
        if (actualYMax == null) return;
        // 与 updateChart/updateTrendChart 一致的强 pad，避免拖动时回到弱 pad
        const isLoss = chart === charts.loss || (chart === charts.latency && mainMetric === "loss");
        const { min, max } = applyYPad(actualYMin, actualYMax,
          isLoss ? { minTop: 0.30, minFloor: 1 } : { minTop: 0.25, minFloor: 2 });
        chart.setOption({ yAxis: chart === charts.latency && mainMetric === "mos" ? { min: 1, max: 5 }
          : (chart === charts.latency && axisMode === "log")
            ? { min: Math.max(0.1, min), max: Math.max(max, Math.max(0.1, min) * 1.2) }
            : { min, max } });
      } catch (e) { /* 静默 */ }
    });
  }

  function updateChart() {
    try {
      const now = Date.now();
      const metric = mainMetric || "latency";
      const focusTarget = selectedHost ? targets.find((x) => targetKey(x) === selectedHost) : null;
      const focusLabel = focusTarget ? targetLabel(focusTarget) : null;
      // y 轴单位 / 名称
      const yMeta = {
        latency: { unit: " ms", name: "ms" },
        loss:    { unit: " %",  name: "%" },
        jitter:  { unit: " ms", name: "ms" },
        std:     { unit: " ms", name: "ms" },
        cv:      { unit: " %",  name: "CV %" },   // 变异系数 0-∞，但家庭网络基本 0-200%
        mos:     { unit: "",    name: "MOS" },    // 1-5 钳制
      };
      const meta = yMeta[metric] || yMeta.latency;
      // 数据源：metric → 数据来源
      //   latency → hist 24h 分钟聚合 + ping buffer 秒级
      //   loss/jitter/std → ping buffer
      //   cv/mos → mosSeries/cvSeries（每 30s 聚合一次）
      const series = [];
      const allData = [];      // 收集所有 series 的 [[x, y], ...] 用于按窗口算 y 轴
      const peakDataAll = [];  // 聚焦目标的峰值曲线，参与纵轴范围计算
      const legendNames = [];  // 图例白名单：峰值曲线不进图例
      let focusedLine = null;  // 被聚焦目标的均值线，纵轴要保证它完整可见
      let peakSeriesCount = 0;
      for (const tgt of targets) {
        const color = colorOf[tgt.host] || "#94a3b8";
        const focusMe = !!selectedHost && selectedHost === targetKey(tgt);
        const dim = (selectedHost && !focusMe) ? 0.1 : 1;
        const h = tgt.host;
        // 数据：每个 metric 用不同数据源。统一为 "hist 分钟历史(24h) + 实时(秒/30s)" 合并，
        // 保证切任意指标都能看到更早的数据（不再只显示最近几分钟）
        const dataKey = { latency: "v", loss: "loss", jitter: "jitter", std: "std" }[metric] || "v";
        // 实时缓冲区（秒级）；它最早的时间戳就是"分钟历史"与"原始点"的分界线
        const rawBuf = metric === "cv" ? cvSeries[h] : metric === "mos" ? mosSeries[h] : ping[h];
        const rawStart = rawBuf && rawBuf.t.length ? rawBuf.t[0] * 1000 : Infinity;
        const lineData = (function () {
          const parts = [];
          // hist 历史：延迟/丢包/抖动/标准差直接取字段；CV/MOS 用公式从 hist 逐点算
          if (hist[h] && hist[h].t && hist[h].t.length) {
            if (metric === "cv" && hist[h].std) {
              const a = hist[h].t.map((x, k) => {
                const sd = hist[h].std[k], av = hist[h].v[k];
                return [x, (av != null && av > 0 && sd != null) ? (sd / av * 100) : null];
              });
              parts.push(...a);
            } else if (metric === "mos" && hist[h].v) {
              const a = hist[h].t.map((x, k) => {
                const av = hist[h].v[k];
                if (av == null) return [x, null];
                const l = hist[h].loss ? hist[h].loss[k] : 0;
                const jt = hist[h].jitter ? hist[h].jitter[k] : 0;
                const mos = 4.5 - 0.005 * av - 0.1 * (l || 0) - 0.005 * (jt || 0);
                return [x, Math.max(1, Math.min(5, mos))];
              });
              parts.push(...a);
            } else if (metric !== "cv" && metric !== "mos" && hist[h][dataKey]) {
              const a = hist[h].t.map((x, k) => [x, hist[h][dataKey][k] != null ? hist[h][dataKey][k] : null]);
              parts.push(...a);
            }
          }
          const historicalCount = parts.length;
          // 实时数据：延迟/丢包/抖动/标准差用 ping buffer（秒级），CV/MOS 用 30s 聚合
          if (metric === "cv" && cvSeries[h]) {
            const b = cvSeries[h].t.map((x, k) => [x * 1000, cvSeries[h].v[k]]);
            parts.push(...b);
          } else if (metric === "mos" && mosSeries[h]) {
            const b = mosSeries[h].t.map((x, k) => [x * 1000, mosSeries[h].v[k]]);
            parts.push(...b);
          } else if (metric !== "cv" && metric !== "mos" && ping[h]) {
            const b = ping[h].t.map((x, k) => [x * 1000, ping[h][dataKey] != null ? ping[h][dataKey][k] : null]);
            parts.push(...b);
          }
          return window.NMCharts.mergeSeries(parts.slice(0,historicalCount),parts.slice(historicalCount),rawStart,
            Math.max(15000,(cur.expectedInterval||1)*3000), (previous)=>previous[0]<Date.now()-86400000?5400000:90000);
        })();
        allData.push(lineData);
        // 峰值曲线只画给"被聚焦的那个目标"：9 条同时铺开会糊成一片，反而看不出结论。
        // 实时段本身就是逐次单包值（本身就是峰值概念），所以只取历史分钟段的 max。
        const focused = focusMe;
        const peakData = (focused && metric === "latency" && hist[h] && hist[h].mx)
          ? hist[h].t.reduce((acc, x, k) => {
              const v = hist[h].mx[k];
              if (v != null && x < rawStart) acc.push([x, v]);
              return acc;
            }, [])
          : [];
        if (peakData.length) peakDataAll.push(peakData);
        if (focused) focusedLine = lineData;
        const historicalLoss = hist[h] ? hist[h].t.filter((ts,i) => ts<rawStart && hist[h].loss?.[i]>0) : [];
        const lossTimes = [...new Set([...(ping[h]?.lossT || []).map((ts)=>ts*1000),...historicalLoss])];
        const s = {
          // 用 stable id（只基于 target name，不带 metric 前缀）：
// 切指标时 id 不变 → ECharts merge 自动用新 data 覆盖旧 series，不会残留旧指标的数据污染 y 轴
id: "line-" + targetKey(tgt),
          name: targetLabel(tgt), type: "line", color,
          data: lineData, showSymbol: lineData.length <= 2, symbolSize: 5, connectNulls: false,
          markPoint: { data: [] },
          lineStyle: { width: focused ? 3 : (selectedHost ? 0.9 : 2.2), opacity: dim },
          itemStyle: { opacity: dim },
        };
        const gapBands=[];
        lineData.forEach((point,index)=>{if(index>0&&index<lineData.length-1&&point[1]==null&&point[0]===lineData[index-1][0]+1)gapBands.push([{name:'缺少采样（原因未知）',xAxis:lineData[index-1][0]},{xAxis:lineData[index+1][0]}]);});
        // 缺采样只画虚线边框，不填色：填色会变成几块大灰柱把曲线盖住。
        // 各目标共用同一套采样时刻，缺口基本重合，所以只让一个系列负责画。
        const isGapCarrier = focused || (!selectedHost && tgt === targets[0]);
        const gapStyle = isGapCarrier
          ? { color: 'transparent', borderColor: 'rgba(148,163,184,.5)', borderWidth: 1, borderType: 'dashed' }
          : { color: 'transparent', borderColor: 'transparent', borderWidth: 0 };
        s.markArea={silent:false,label:{show:false},itemStyle:gapStyle,tooltip:{formatter:()=> '该区间缺少采样，不能据此判断丢包'},data:gapBands};
        // 丢包红点：只在 latency/loss 指标下显示（其他指标定位下显示无意义）
        const showLossPoint = metric === "latency" || metric === "loss";
        if (showLossPoint && lossTimes.length) {
          s.markPoint = {
            symbol: "circle", symbolSize: 7,
            data: lossTimes.map((t) => ({ name: "丢包", coord: [t, 0] })),
            itemStyle: { color: "#f87171", opacity: dim },
            label: { show: false }, z: 5,
          };
        }
        if (showLossPoint && ping[h]?.state) {
          ping[h].state.forEach((state,index)=>{if(['skipped','local_error','resolution_error','cancelled'].includes(state))s.markPoint.data.push({name:'未采样：'+state,symbol:'rect',symbolSize:6,itemStyle:{color:'#94a3b8'},coord:[ping[h].t[index]*1000,0]});});
        }
        legendNames.push(s.name);
        series.push(s);
        // 峰值曲线：同色虚线，只跟被聚焦的那条走，表达"这一分钟最坏到过多少"
        if (peakData.length) {
          peakSeriesCount += 1;
          series.push({
            id: "peak-" + targetKey(tgt),
            name: s.name + " · 峰值",
            type: "line", color, data: peakData, z: 4,
            symbol: "none", connectNulls: false, showSymbol: false,
            lineStyle: { width: 1.6, type: "dashed", opacity: 0.9 },
            itemStyle: { color },
          });
        }
      }
      // 按"当前可见窗口"算 y 轴：用 xAxis[0].min/max（ECharts 拖动后自动同步的 epoch 值）
      const effectiveWindow = follow
        ? resolveViewWindow({mode: "follow", span_ms: viewSpanMs}, latestTime(allData), now)
        : manualWindow || currentXWindow(charts.latency, now);
      let { xStart, xEnd } = effectiveWindow;
      let { yMin, yMax } = yBoundsInWindow(allData, xStart, xEnd);
      // 当前窗口无数据时（拖到远古时段超出 buffer），用全数据 yMax 兜底，避免 yMax=null
      // 走 fallback 0-100/0-5 把 y 轴拉得比数据还大造成溢出假象
      if (yMax == null) {
        ({ yMin, yMax } = yBoundsInWindow(allData, null, null));
      }
      // 纵轴范围
      //   聚焦某目标：该目标的均值线 + 峰值虚线必须完整可见（这是用户此刻要看的）
      //   未聚焦：按 axisMode，typical 用 p95 让日常波动看得清，extreme 装下全部
      const valuesInWindow = (lists) => {
        const out = [];
        for (const arr of lists) {
          for (const p of arr) {
            if (!Array.isArray(p) || typeof p[1] !== "number" || !isFinite(p[1])) continue;
            if (p[0] < xStart || p[0] > xEnd) continue;
            out.push(p[1]);
          }
        }
        return out;
      };
      const pct = (arr, r) => {
        if (!arr.length) return null;
        const s = arr.slice().sort((a, b) => a - b);
        return s[Math.min(s.length - 1, Math.max(0, Math.round(r * (s.length - 1))))];
      };
      if (metric !== "mos" && yMax != null) {
        const all = valuesInWindow(allData.concat(peakDataAll));
        if (focusedLine) {
          const fb = yBoundsInWindow([focusedLine].concat(peakDataAll), xStart, xEnd);
          const others = valuesInWindow(allData.filter((d) => d !== focusedLine));
          const a = [fb.yMax, pct(others, 0.95)].filter((v) => v != null);
          const b = [fb.yMin, pct(others, 0.05)].filter((v) => v != null);
          if (a.length) yMax = Math.max(...a);
          if (b.length) yMin = Math.min(...b);
        } else if (axisMode === "typical") {
          const hi = pct(all, 0.95), lo = pct(all, 0.05);
          if (hi != null && hi > 0) yMax = hi;
          if (lo != null) yMin = lo;
        } else if (all.length) {
          // 对数轴 / 含极值：用完整范围，不裁任何数据
          yMax = Math.max(...all);
          yMin = Math.min(...all);
        }
        if (!(yMax > yMin)) yMax = yMin + 1;
      }

      // y 轴：对数轴能让 8ms 的网关和 2000ms 的偶发尖峰同屏可见；线性轴按常态/含极值二选一
      const useLog = axisMode === "log" && metric !== "mos" && metric !== "loss";
      const yAxis = { type: useLog ? "log" : "value", scale: !useLog, logBase: 10,
        name: meta.name, nameTextStyle: { color: t().label },
        axisLabel: { color: t().label, fontSize: chartFontSize(11), formatter: (v) => fmtVal(v, "") },
        splitLine: { lineStyle: { color: t().split } },
        splitNumber: 4,
      };
      if (useLog) {
        const lo = Math.max(yMin == null ? 1 : yMin, 0.1);
        const hi = Math.max(yMax == null ? lo * 10 : yMax, lo * 1.5);
        yAxis.min = Math.max(0.1, lo * 0.85);
        yAxis.max = hi * 1.15;
      } else if (yMax !== null) {
        if (metric === "mos") {
          yAxis.min = 1; yAxis.max = 5;        // MOS 固定 [1, 5]
        } else {
          const opts = metric === "loss"
            ? { minTop: 0.30, minFloor: 1 }    // 丢包 % 多留空间，9.46% 时 max ≈ 12
            : metric === "cv"
              ? { minTop: 0.30, minFloor: 3 }  // CV %
              : { minTop: 0.25, minFloor: 2 }; // 延迟/抖动/标准差 ms
          const p = applyYPad(yMin, yMax, opts);
          yAxis.min = p.min; yAxis.max = p.max;
        }
      } else {
        yAxis.min = metric === "mos" ? 1 : 0;
        yAxis.max = metric === "mos" ? 5 : (metric === "loss" ? 5 : 100);
      }
      for (const line of series) {
        for (const point of (line.markPoint && line.markPoint.data) || []) point.coord[1] = yAxis.min;
      }
      const opt = {
        animation: false, animationDuration: 0, animationDurationUpdate: 0,
        grid: { left: 58, right: 18, top: 52, bottom: 56 },
        xAxis: baseAxis(),
        yAxis,
        tooltip: Object.assign({
          trigger: "axis",
          confine: true,
          axisPointer: { type: "cross", lineStyle: { color: t().axis } },
          valueFormatter: (v) => fmtVal(v, meta.unit),
          formatter: (parameters) => {
            const listed=Array.isArray(parameters)?parameters:[parameters];
            // 聚焦时只显示该目标的条目，避免一次弹出九行看不过来
            const points=focusLabel?listed.filter(point=>String(point.seriesName||"").replace(/ · 峰值$/,"")===focusLabel):listed;
            if (!points.length) return "";
            return points.map(point=>{
              const shownName=String(point.seriesName||"");
              const isPeak=/ · 峰值$/.test(shownName);
              const baseName=isPeak?shownName.replace(/ · 峰值$/,""):shownName;
              const host=targets.find(target=>targetLabel(target)===baseName)?.host;
              const ts=point.data?.[0],index=host&&hist[host]?hist[host].t.indexOf(ts):-1;
              const raw=host&&(metric==='cv'?cvSeries[host]:metric==='mos'?mosSeries[host]:ping[host]);
              const isRaw=raw?.t?.includes(ts/1000);
              const info=!isRaw&&index>=0?hist[host].meta?.[index]:null;
              const scope=isPeak?'该分钟内最坏一次':
                info?(info.resolution==='hour'?'小时聚合':'分钟聚合')+(info.sample_count!=null?' · '+info.sample_count+'样本':' · 样本数未知')+(info.approximate?' · 近似':''):
                metric==='std'||metric==='loss'?'最近'+(cur.statWindow||60)+'轮统计':metric==='cv'||metric==='mos'?'最近30秒质量估算':'单次采样';
              return escapeHtml(shownName)+': '+fmtVal(point.data?.[1],meta.unit)+'<br><small>'+scope+'</small>';
            }).join('<br>');
          },
        }, tip()),
        // legend 只列主曲线：峰值散点是辅助信息，列进图例会翻倍
        legend: {
          type: 'scroll', left: 54, right: 20, pageIconSize: 10,
          data: legendNames,
          textStyle: { color: t().label, fontSize: chartFontSize(12) },
          top: 6, icon: "circle", itemWidth: 9, itemHeight: 9,
        },
        series,
        // dataZoom 始终传（保留 slider 控件）；follow=false 时不设 startValue/endValue → 保留用户拖动位置
        dataZoom: [
          { type: "inside", xAxisIndex: 0, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
          { type: "slider", xAxisIndex: 0, bottom: 4, height: 20, brushSelect: false,
            borderColor: t().axis, backgroundColor: "transparent",
            fillerColor: accentColor() + "2e", handleStyle: { color: accentColor() },
            textStyle: { color: t().label, fontSize: chartFontSize(11) },
            // 限制最大跨度 24h（后端分钟聚合保留时长），可拖回看历史；最小 30s 防止放大到秒级
            maxValueSpan: (Number($("historyHours")?.value) || 24) * 3600 * 1000, minValueSpan: 30000 },
        ],
      };
      if (follow || manualWindow) {
        for (const zoom of opt.dataZoom) {
          zoom.startValue = effectiveWindow.xStart;
          zoom.endValue = effectiveWindow.xEnd;
        }
      }
      const followButton = $("btnFollow");
      followButton.textContent = follow ? "跟随中" : "恢复跟随";
      followButton.setAttribute("aria-pressed", String(follow));
      const rangeLabel=$('viewRange');
      if (rangeLabel) {
        const parts=[follow?'跟随最新':'浏览历史',
          new Date(effectiveWindow.xStart).toLocaleString('zh-CN')+' — '+new Date(effectiveWindow.xEnd).toLocaleString('zh-CN'),
          '最近10分钟为原始采样，更早为每分钟聚合'];
        if (metric==='latency') parts.push(focusLabel
          ? (peakSeriesCount ? ('已聚焦 '+focusLabel+'，虚线是该分钟最坏值') : ('已聚焦 '+focusLabel+'，这段全是逐次原始采样'))
          : '点左侧目标行可展开该目标的峰值虚线');
        rangeLabel.textContent=parts.join(' · ');
      }
      charts.latency.setOption(opt, { notMerge: false, replaceMerge: ["series"] });
    } catch (e) {
      console.error("[chartLatency]", e);
    }
  }

  charts.latency.on("datazoom", () => {
    const option = charts.latency.getOption();
    const zoom = (option.dataZoom || []).find((z) => z.type === "slider");
    const window = zoom && Number.isFinite(zoom.startValue) && Number.isFinite(zoom.endValue)
      ? { xStart: zoom.startValue, xEnd: zoom.endValue }
      : currentXWindow(charts.latency, Date.now());
    const latest = latestTime((option.series || []).map((series) => series.data || []));
    if (zoom && Number.isFinite(zoom.startValue) && Number.isFinite(zoom.endValue)) {
      manualWindow = window;
      viewSpanMs = Math.max(30000, window.xEnd - window.xStart);
    }
    follow = latest != null && window.xEnd >= latest - 250;
    if (follow) manualWindow = null;
    $("btnFollow").textContent = follow ? "跟随中" : "恢复跟随";
    $("btnFollow").setAttribute("aria-pressed", String(follow));
  });

  // ---------- 通用时间序列图（丢包/抖动/TCP/DNS/流量），每目标独立颜色 + 时间缩放条 ----------
  // source: { label -> {t:[ms], v:[...]} }
  function updateTrendChart(chart, source, yName, fmt) {
    try {
      const series = [];
      const allData = [];
      Object.keys(source).forEach((label, i) => {
        const d = source[label];
        if (!d || !d.t || !d.t.length) return;
        const color = d.color || colorForLabel(label) || PALETTE[i % PALETTE.length];
        const data = d.t.map((x, k) => [x, d.v[k]]);
        allData.push(data);
        series.push({
          id: chart.id + "-" + label, name: d.label || label, type: "line",
          color, showSymbol: data.length <= 2, symbolSize: 5, connectNulls: false,
          lineStyle: { width: 1.6 }, itemStyle: { color },
          data,
        });
      });
      // 按当前可见窗口算 y 轴：xAxis[0].min/max 优先（拖动后 ECharts 自动同步）
      const nowT = Date.now();
      let { xStart, xEnd } = currentXWindow(chart, nowT);
      const dz = (chart.getOption().dataZoom || []).find((z) => z.type === "slider");
      const newest = latestTime(allData);
      if (!dz || dz.end == null || dz.end >= 99.9) xEnd = newest ?? nowT;
      let { yMin, yMax } = yBoundsInWindow(allData, xStart, xEnd);
      if (yMax == null) {
        ({ yMin, yMax } = yBoundsInWindow(allData, null, null));
      }

      const yAxis = { type: "value", scale: true, name: yName,
        nameTextStyle: { color: t().label },
        axisLabel: { color: t().label, fontSize: chartFontSize(11) },
        splitLine: { lineStyle: { color: t().split } },
        splitNumber: 4,
      };
      if (yMax !== null) {
        // 有数据（含全 0/全稳定的情况，yMax=0 也算有效）→ 按窗口实际范围算 pad
        const isPct = yName === "%";
        const p = applyYPad(yMin, yMax, isPct ? { minTop: 0.30, minFloor: 1 }
                                              : { minTop: 0.25, minFloor: 2 });
        yAxis.min = p.min; yAxis.max = p.max;
      } else {
        // 完全无数据时的合理默认值（按指标给最小可读范围）
        yAxis.min = 0;
        yAxis.max = yName === "%" ? 5 : 5;
      }
      const times = allData.flatMap((data) => data.map((pair) => pair[0]));
      const oneTime = times.length < 2 || Math.min(...times) === Math.max(...times);
      chart.setOption({
        animation: false, animationDuration: 0, animationDurationUpdate: 0,
        grid: { left: 48, right: 16, top: 44, bottom: 42 },
        xAxis: { ...baseAxis(), min: oneTime ? nowT - DEFAULT_VIEW_MS : null, max: oneTime ? nowT + 2000 : null },
        yAxis,
        tooltip: Object.assign({ trigger: "axis", valueFormatter: fmt }, tip()),
        legend: { type:'scroll',left:48,right:14,pageIconSize:9,textStyle: { color: t().label, fontSize: chartFontSize(12) },
          top: 2, icon: "circle", itemWidth: 8, itemHeight: 8 },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, zoomOnMouseWheel: true, moveOnMouseMove: true },
          { type: "slider", xAxisIndex: 0, bottom: 0, height: 18,
            showDetail:false,
            borderColor: t().axis, backgroundColor: "transparent",
            fillerColor: accentColor() + "26", handleStyle: { color: accentColor() },
            textStyle: { color: t().label, fontSize: chartFontSize(11) },
            maxValueSpan: (Number($("historyHours")?.value) || 24) * 3600 * 1000, minValueSpan: 30000 },
        ],
        series,
      }, { notMerge: false, replaceMerge: ["series"] });   // merge：保留 legend 选中 / dataZoom 状态
    } catch (e) {
      console.error("[trend]", chart.id, e);
    }
  }

  function colorForLabel(label) {
    const t = targets.find((x) => x.name === label);
    if (t) return colorOf[t.host] || null;
    return null;
  }

  // 从 ping 数据按 key 构建 {目标名 -> {t,v}}，t 单位毫秒
  function pingSource(key) {
    const s = {};
    for (const t of targets) {
      const d = ping[t.host];
      if (!d || !d.t || !d.t.length) continue;
      s[targetKey(t)] = { label: targetLabel(t), color: colorOf[t.host], t: d.t.map((x) => x * 1000), v: d[key] };
    }
    return s;
  }

  function updateBars() {
    updateTrendChart(charts.loss, pingSource("loss"), "%",
      (v) => (v == null ? "--" : v.toFixed(1) + " %"));
    updateTrendChart(charts.jitter, pingSource("jitter"), "ms", (v) => fmtMs(v));
    updateTrendChart(charts.tcp, tcpData, "ms", (v) => fmtMs(v));
    updateTrendChart(charts.dns, dnsData, "ms", (v) => fmtMs(v));
    updateTrendChart(charts.traffic, {
      "下载": { t: traffic.t.map((x) => x * 1000), v: traffic.rx },
      "上传": { t: traffic.t.map((x) => x * 1000), v: traffic.tx },
    }, "B/s", (v) => fmtBps(v));
  }

  // ---------- 24h 历史图 ----------
  let lastHistSig = "";
  let historyRequest = 0;
  async function loadHistory() {
    const request = ++historyRequest;
    const hours = Number($("historyHours")?.value) || 24;
    try {
      const j = await readJSON("/api/history?metric=ping&hours=" + hours);
      if (request !== historyRequest) return;
      if (!j.rows) return;
      // 数据签名：60s 拉一次，没新数据不重建（避免"每几十秒闪一下"）
      const sig = theme + ":" + JSON.stringify(j.rows);
      if (sig === lastHistSig) return;
      lastHistSig = sig;
      const by = {};
      for (let rowIndex=0;rowIndex<j.rows.length;rowIndex++) {
        const row=j.rows[rowIndex];
        // rows: [ts, target, avg, min, max, loss, jitter, std] —— 全部存入 hist，
        // 供主图其他指标（丢包/抖动/标准差/CV/MOS）也能回溯 24h 分钟聚合历史
        const [ts, target, avg, mn, mx, loss, jitter, std] = row;
        const e = (by[target] = by[target] || { t: [], v: [], mn: [], mx: [], loss: [], jitter: [], std: [], meta: [] });
        e.meta.push(j.metadata?.[rowIndex] || {resolution:ts<Date.now()/1000-86400?'hour':'minute',approximate:true});
        e.t.push(ts * 1000); e.v.push(avg);
        // 后端每分钟都存了 min/max，之前只画了 avg，尖峰在回看时被平均抹平。
        e.mn.push(mn); e.mx.push(mx);
        e.loss.push(loss); e.jitter.push(jitter); e.std.push(std);
      }
      for (const h of Object.keys(hist)) delete hist[h];
      for (const [h, d] of Object.entries(by)) hist[h] = d;
      const names = Object.keys(by);
      charts.histLat.setOption({
        animation: false, animationDuration: 0, animationDurationUpdate: 0,
        grid: baseGrid, xAxis: baseAxis(),
        yAxis: yValue("ms"),
        tooltip: Object.assign({
          trigger: "axis", valueFormatter: (v) => fmtVal(v, " ms"),
        }, tip()),
        legend: { textStyle: { color: t().label, fontSize: chartFontSize(12) }, top: 0, icon: "circle", itemWidth: 9, itemHeight: 9 },
        series: names.map((n, i) => ({
          id: "hist-" + n,
          name: n, type: "line", showSymbol: false, connectNulls: false,
          color: colorOf[n] || PALETTE[i % PALETTE.length], lineStyle: { width: 1.4 },
          data: by[n].t.map((x, k) => [x, by[n].v[k]]),
        })),
      }, { notMerge: false, replaceMerge: ["series"] });   // 增量合并：series id 稳定，只更新数据不重建
      const lossBy = {};
      for (const row of j.rows) {
        const [ts, target, , , , loss] = row;
        if (!loss || loss <= 0) continue;
        (lossBy[target] = lossBy[target] || []).push([ts * 1000, loss]);
      }
      charts.histLoss.setOption({
        animation: false, animationDuration: 0, animationDurationUpdate: 0,
        grid: baseGrid, xAxis: baseAxis(),
        yAxis: yValue("%"),
        tooltip: Object.assign({
          trigger: "item", valueFormatter: (v) => fmtVal(v, " %"),
        }, tip()),
        // 24h 丢包散点：按 IP 分色（与主图同一套 colorOf 配色，同一个 IP 颜色一致）
        series: Object.entries(lossBy).map(([n, d], i) => ({
          id: "histloss-" + n,
          name: n, type: "scatter", symbolSize: 7,
          itemStyle: { color: colorOf[n] || PALETTE[i % PALETTE.length] },
          data: d,
        })),
      }, { notMerge: false, replaceMerge: ["series"] });
      updateChart();
    } catch (e) { console.warn("历史数据读取失败", e); }
  }

  // ---------- 数据处理 ----------
  // 每 30 秒用真实时间窗口聚合，采样间隔变化不会把旧数据算入当前质量。
  const AGG_WINDOW = 30;
  const AGG_KEEP = 600;
  let lastAggregateTs = 0;
  let aggregateTimer = null;
  function aggregateQuality(now) {
    const hosts = new Set(targets.map((target) => target.host));
    for (const host of hosts) {
      const d = ping[host];
      if (!d) continue;
      const indices = d.t.map((ts, i) => ts >= now - AGG_WINDOW && ts <= now && !["skipped","local_error","resolution_error","cancelled"].includes(d.state?.[i]) ? i : -1)
        .filter((i) => i >= 0);
      const values = indices.map((i) => d.v[i]).filter(Number.isFinite);
      let mos = null, cv = null;
      if (values.length) {
        const avg = mean(values);
        const std = Math.sqrt(mean(values.map((value) => (value - avg) ** 2)));
        cv = avg > 0 ? std / avg * 100 : 0;
        const failures = indices.filter((i) => d.v[i] == null).length;
        const lossPct = failures / indices.length * 100;
        const jitter = indices.filter((i) => Number.isFinite(d.v[i]))
          .map((i) => d.jitter[i]).filter(Number.isFinite);
        const jitterAvg = mean(jitter) ?? 0;
        mos = Math.max(1, Math.min(5, 4.5 - 0.005 * avg - 0.1 * lossPct - 0.005 * jitterAvg));
      }
      mosSeries[host] = mosSeries[host] || { t: [], v: [] };
      cvSeries[host] = cvSeries[host] || { t: [], v: [] };
      push(mosSeries[host].t, now, AGG_KEEP);
      push(mosSeries[host].v, mos == null ? null : +mos.toFixed(2), AGG_KEEP);
      push(cvSeries[host].t, now, AGG_KEEP);
      push(cvSeries[host].v, cv == null ? null : +cv.toFixed(1), AGG_KEEP);
    }
  }

  function acceptEpoch(item) {
    const epoch=item.time_epoch||0;
    if(epoch<(cur.timeEpoch||0))return false;
    if(epoch>(cur.timeEpoch||0)) {
      for(const map of [ping,hist,mosSeries,cvSeries,tcpData,dnsData])for(const key of Object.keys(map))delete map[key];
      traffic.t=[];traffic.rx=[];traffic.tx=[];cur.lastPingTs=0;cur.timeEpoch=epoch;lastChartTs=0;lastAggregateTs=0;clearTimeout(aggregateTimer);
    }
    return true;
  }
  function onPing(item) {
    if(!acceptEpoch(item))return;
    cur.lastReceiveMono=typeof performance!=='undefined'?performance.now():Date.now();
    cur.lastPingTs = Math.max(cur.lastPingTs || 0, item.ts);
    const s = ping[item.host] || (ping[item.host] = { t: [], v: [], loss: [], jitter: [], std: [], lossT: [], state: [] });
    if (s.t.length && item.ts < s.t[s.t.length - 1]) return;
    s.state = s.state || [];
    const same = s.t.indexOf(item.ts);
    if (same >= 0) {
      if (item.kind === "base") {
        s.v[same] = item.avg; s.loss[same] = item.loss; s.jitter[same] = item.jitter; s.std[same] = item.std; s.state[same] = item.state;
        s.last = item;
        s.lossT = s.lossT.filter((ts) => ts !== item.ts);
        if (item.avg == null && !["skipped","local_error","resolution_error","cancelled"].includes(item.state)) push(s.lossT, item.ts, REAL_KEEP);
        updateTargetCard(item); updateTableRow(item); updateOverall(); maybeUpdateCharts();
      }
      return;
    }
    s.last = item;
    push(s.t, item.ts, REAL_KEEP);
    push(s.v, item.avg, REAL_KEEP);
    push(s.loss, item.loss, REAL_KEEP);
    push(s.jitter, item.jitter, REAL_KEEP);
    push(s.std, item.std, REAL_KEEP);
    push(s.state, item.state, REAL_KEEP);
    while (s.t.length > 1 && s.t[0] < item.ts - 600) for (const key of ["t","v","loss","jitter","std","state"]) s[key].shift();
    s.lossT = s.lossT.filter((ts) => ts >= s.t[0]);
    if (item.avg == null && !["skipped","local_error","resolution_error","cancelled"].includes(item.state)) push(s.lossT, item.ts, REAL_KEEP);
    updateTargetCard(item);
    updateTableRow(item);
    updateOverall();
    maybeUpdateCharts();
    // 每 30s 触发一次 MOS/CV 聚合（无论主图指标是什么都计算，避免切换指标时没数据）
    const nowSec = Date.now() / 1000;
    if (nowSec - lastAggregateTs > 30) {
      clearTimeout(aggregateTimer);
      aggregateTimer = setTimeout(() => {
        lastAggregateTs = Date.now() / 1000;
        aggregateQuality(lastAggregateTs);
        if (mainMetric === "cv" || mainMetric === "mos") updateChart();
      }, 50);
    }
  }

  function updateOverall() {
    const bases = targets.filter((x) => x.kind === "base" && ping[x.host]);
    const last = (h) => { const d = ping[h]; return d.v[d.v.length - 1]; };
    const avgs = bases.map((x) => last(x.host)).filter((v) => v != null);
    const losses = bases.map((x) => { const d = ping[x.host]; return d.loss[d.loss.length - 1]; });
    const a = mean(avgs), l = mean(losses);
    const parts = [];
    if (a != null) parts.push(`平均延迟 <b>${fmtMs(a)}</b>`);
    if (l != null) parts.push(`平均丢包 <b>${l.toFixed(1)}%</b>`);
    $("overallInfo").innerHTML = parts.join(" · ") ||
      `<span style="color:var(--muted)">分 IP 数据见下方表格</span>`;
  }

  function maybeUpdateCharts() {
    const now = Date.now();
    if (now - lastChartTs < 1000) return;   // 每秒最多全量重绘一次，避免闪烁
    lastChartTs = now;
    if (metricVisible("latency")) updateChart();
    if (metricVisible("loss") || metricVisible("jitter") ||
        metricVisible("traffic") || metricVisible("tcp") || metricVisible("dns")) updateBars();
  }

  function onDns(item) {
    if(!acceptEpoch(item))return;
    cur.dnsPool = cur.dnsPool || {};
    if (item.ms != null) cur.dnsPool[item.server] = item.ms;
    else delete cur.dnsPool[item.server];
    const s = dnsData[item.server] || (dnsData[item.server] = { t: [], v: [] });
    if (s.t.length && item.ts * 1000 <= s.t[s.t.length - 1]) return;
    push(s.t, item.ts * 1000, REAL_KEEP);
    push(s.v, item.ms, REAL_KEEP);
    maybeUpdateCharts();
  }

  function onTcp(item) {
    if(!acceptEpoch(item))return;
    const s = tcpData[item.name] || (tcpData[item.name] = { t: [], v: [] });
    if (s.t.length && item.ts * 1000 <= s.t[s.t.length - 1]) return;
    push(s.t, item.ts * 1000, REAL_KEEP);
    push(s.v, item.ms, REAL_KEEP);
    maybeUpdateCharts();
  }

  function onTraffic(item) {
    if(!acceptEpoch(item))return;
    if (traffic.t.length && item.ts <= traffic.t[traffic.t.length - 1]) return;
    push(traffic.t, item.ts, REAL_KEEP);
    push(traffic.rx, item.rx_bps, REAL_KEEP);
    push(traffic.tx, item.tx_bps, REAL_KEEP);
    maybeUpdateCharts();
  }

  function onStatus(status) {
    cur.status = status;
    $("statusDot").className = "dot " + status;
    $("statusText").textContent = STATUS[status] || status;
  }

  function onTrace(msg) {
    traces[msg.target] = msg.hops;
    renderPath();
    refreshTargets();
  }

  function applyOverview(ov) {
    if(!ov)return;
    if(cur.expectedSession && ov.session_id && cur.expectedSession!==ov.session_id) {
      for(const map of [ping,hist,mosSeries,cvSeries,tcpData,dnsData])for(const key of Object.keys(map))delete map[key];
      traffic.t=[];traffic.rx=[];traffic.tx=[];cur.lastPingTs=0;cur.timeEpoch=0;
    }
    cur.expectedSession=ov.session_id||cur.expectedSession;
    if(ov.time_epoch!=null)acceptEpoch({time_epoch:ov.time_epoch});
    if(cur.probeRevision!=null && ov.probe_revision!==cur.probeRevision) {
      for(const map of [tcpData,dnsData])for(const key of Object.keys(map))delete map[key];traffic.t=[];traffic.rx=[];traffic.tx=[];
    }
    cur.probeRevision=ov.probe_revision;
    cur.summary=ov.summary;cur.target_summaries=ov.target_summaries||{};
    const storageWarning=$('storageStatus');
    if(storageWarning){storageWarning.hidden=!(ov.storage?.error||ov.storage?.write_age>10);storageWarning.textContent=storageWarning.hidden?'':'历史保存异常，实时数据仍在更新；未保存数据可能不完整';}
    cur.traceEnabled=ov.trace_enabled;cur.freshFor=ov.probe_health?.ping?.fresh_for||15;
    cur.expectedInterval=ov.expected_interval||1;cur.statWindow=ov.stat_window||60;cur.thresholds=ov.quality_thresholds||cur.thresholds;
    if(ov.targets&&JSON.stringify(ov.targets)!==JSON.stringify(targets))setTargets(ov.targets);
    if(ov.traces)traces=ov.traces;renderPath();
    if(ov.configuration_error&&cur.configurationError!==ov.configuration_error)toast('warning',ov.configuration_error);
    cur.configurationError=ov.configuration_error;
    if(ov.db_size!=null)$('dbSize').textContent=ov.db_size+' MB';
    refreshTableSummaries();updateOverall();
  }
  async function refreshTargets() {
    try{const ov=await fetchOverview();applyOverview(ov);return ov;}
    catch(error){toast('warning','刷新目标失败：'+error.message);return null;}
  }

  // 刷新表格 MOS/CV/可用率列（target_summaries 更新后调用）
  function renderSummaryCells(row, summary) {
    const set=(cls,value,bad,warn)=>{const cell=row.querySelector('.'+cls);if(cell){cell.textContent=value;cell.className=cls+(bad?' bad':warn?' warn':'');}};
    const mos=summary.mos,cv=summary.cv,av=summary.availability,avg=summary.latency_avg;
    set('v-mos',mos==null?'--':mos.toFixed(2),mos!=null&&mos<3.6,mos!=null&&mos>=3.6&&mos<4);
    set('v-cv',cv==null?'--':cv.toFixed(1)+'%',cv!=null&&cv>80);
    set('v-avail',av==null?'--':av.toFixed(1)+'%',av!=null&&av<95);
    set('v-avg',avg==null?'--':Math.round(avg),avg!=null&&avg>(cur.thresholds?.latency_bad||300));
  }
  function refreshTableSummaries() {
    for(const target of targets)document.querySelectorAll(`#targetTable tr[data-host="${CSS.escape(target.host)}"]`).forEach(row=>renderSummaryCells(row,(cur.target_summaries||{})[target.host]||{}));
  }

  // ---------- 事件 / 告警 ----------
  let audioContext=null;
  function beep(times) {
    if (!soundOn) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = audioContext || (audioContext = new Ctx());
      if(ctx.state==='suspended')ctx.resume();
      const play = (t0) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination);
        o.frequency.value = 880; g.gain.value = 0.18;
        o.start(t0);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.35);
        o.stop(t0 + 0.35);
      };
      play(ctx.currentTime);
      if (times > 1) play(ctx.currentTime + 0.45);
    } catch (e) { /* 忽略 */ }
  }

  let toastTimer = null;
  function toast(level, text) {
    const el = $("toast");
    el.className = "toast " + level;
    el.textContent = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 6000);
  }

  function addEvent(ts, level, text) {
    const li = document.createElement("li");
    li.className = level || "";
    const time = document.createElement("time");
    time.textContent = fmtClock(ts);
    const msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = text;
    li.appendChild(time); li.appendChild(msg);
    const list = $("eventList");
    list.appendChild(li);
    while (list.children.length > 100) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }

  function onAlert(a) {
    const lv = a.level || LEVEL[a.etype] || "info";
    addEvent(a.ts, lv, a.message);
    if (a.notify === false) return;
    if (lv === "critical") { toast("critical", "⚠ " + a.message); beep(2); }
    else if (lv === "warning") { toast("warning", a.message); beep(1); }
    else toast("info", a.message);
  }

  // ---------- 主题 ----------
  function setTheme(mode) {
    theme = mode;
    document.documentElement.dataset.theme = mode;
    try { localStorage.setItem("nm-theme", mode); } catch (e) { /* 浏览器可能禁用存储 */ }
    $("btnTheme").textContent = mode === "dark" ? "浅色" : "深色";
    // 全部图表重绘
    updateChart();
    updateBars();
    loadHistory();
  }
  $("btnTheme").addEventListener("click", () => setTheme(theme === "dark" ? "light" : "dark"));
  $("btnTheme").textContent = theme === "dark" ? "浅色" : "深色";

  // ---------- 设置面板 ----------
  async function openSettings() {
    try {
      const j = await readJSON("/api/config");
      const form = $("settingsForm");
      form.innerHTML = "";
      for (const [k, s] of Object.entries(j.schema)) {
        const row = document.createElement("div");
        row.className = "settings-row";
        if (s.type === "bool") {
          // 开关型：checkbox + label 复选
          row.innerHTML =
            `<label for="setting-${escapeHtml(k)}">${escapeHtml(s.label)}</label>` +
            `<input id="setting-${escapeHtml(k)}" type="checkbox" data-key="${escapeHtml(k)}" data-bool="1" ${j.settings[k] ? "checked" : ""}>` +
            `<span class="unit"></span>`;
        } else {
          row.innerHTML =
            `<label for="setting-${escapeHtml(k)}">${escapeHtml(s.label)}</label>` +
            `<input id="setting-${escapeHtml(k)}" type="number" required data-key="${escapeHtml(k)}" value="${j.settings[k]}" min="${s.min}" max="${s.max}" step="${s.step}">` +
            `<span class="unit">${s.unit}</span>`;
        }
        form.appendChild(row);
      }
      renderTargetList();
      settingsOpener = document.activeElement;
      $("settingsModal").classList.remove("hidden");
      $('settingsError').textContent='';
      $('settingsTitle').focus({preventScroll:true});
      const box=document.querySelector('.modal-box');if(box)box.scrollTop=0;
      window.dispatchEvent(new Event('nm-settings-open'));
    } catch (e) { toast("info", "读取设置失败"); }
  }

  function renderTargetList() {
    const ul = $("targetList");
    ul.innerHTML = "";
    for (const x of targets) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "tl-name";
      name.textContent = x.name;
      const host = document.createElement("span");
      host.className = "tl-host";
      host.textContent = x.host;
      const tag = document.createElement("span");
      tag.className = "tl-tag";
      tag.textContent = x.kind === "hop" ? "路径跳" : (x.protected ? "系统" : "");
      li.append(name, host, tag);
      if (!x.protected && x.kind !== "hop") {
        const btn = document.createElement("button");
        btn.className = "tl-del";
        btn.textContent = "删";
        btn.dataset.host = x.host;
        li.appendChild(btn);
      }
      ul.appendChild(li);
    }
    ul.querySelectorAll(".tl-del").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const host = btn.dataset.host;
        try {
        const r = await apiFetch("/api/targets/remove", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host }),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || j.detail || "请求失败");
        toast(j.ok ? "info" : "warning", j.msg);
        if (j.ok) { await refreshTargets(); renderTargetList(); }
        } catch (e) { toast("critical", "删除失败：" + e.message); }
      });
    });
  }

  $("btnSettings").addEventListener("click", openSettings);
  let settingsOpener = null;
  function closeSettings() {
    $("settingsModal").classList.add("hidden");
    if (settingsOpener) settingsOpener.focus();
  }
  $("btnClose").addEventListener("click", closeSettings);
  $('btnCloseTop')?.addEventListener('click',closeSettings);
  document.addEventListener("keydown", (e) => {
    if ($("settingsModal").classList.contains("hidden")) return;
    if (e.key === "Escape") { e.preventDefault(); closeSettings(); }
    if (e.key === "Tab") {
      const items = Array.from($("settingsModal").querySelectorAll('button, input, select, [tabindex="0"]')).filter((x) => !x.disabled);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  $("btnSave").addEventListener("click", async () => {
    const body = {};
    document.querySelectorAll("#settingsForm input").forEach((inp) => {
      const k = inp.dataset.key;
      if (inp.dataset.bool) body[k] = inp.checked;
      else body[k] = parseFloat(inp.value);
    });
    if (Array.from(document.querySelectorAll("#settingsForm input")).some((inp) => !inp.checkValidity())) {
      $('settingsError').textContent='请填写范围内的有效设置值；无效项已标出。';
      document.querySelectorAll('#settingsForm input').forEach(inp=>inp.setAttribute('aria-invalid',String(!inp.checkValidity())));
      toast('warning','请填写范围内的有效设置值');return;
    }
    try {
      const r = await apiFetch("/api/config", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const result = await r.json();
      if (!r.ok || result.error || result.detail) throw new Error(result.error || result.detail || "保存失败");
      await refreshTargets();
      toast("info", "设置已保存并生效");
      closeSettings();
    } catch (e) { $('settingsError').textContent='保存失败：'+e.message;toast("critical", "保存设置失败："+e.message); }
  });
  $("btnSaveAiKey").addEventListener("click", async () => {
    const k = $("aiKeyInput").value.trim();
    if (!k) { toast("warning", "请填写 API Key"); return; }
    try {
      const r = await apiFetch("/api/ai-key", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: k }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || j.detail || "请求失败");
      toast(j.ok ? "info" : "critical", j.msg);
      if (j.ok) { $("aiKeyInput").value = ""; $("aiKeyInput").placeholder = "已保存（输入新 Key 可覆盖）"; }
    } catch (e) { toast("critical", "保存失败：" + e.message); }
  });
  $("btnClearAiKey").addEventListener("click", async () => {
    try {
      const r = await apiFetch("/api/ai-key", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "" }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || j.detail || "请求失败");
      toast(j.ok ? "info" : "critical", j.msg);
    } catch (e) { toast("critical", "清除失败"); }
  });
  $("btnAddTarget").addEventListener("click", async () => {
    const name = $("newTargetName").value.trim();
    const host = $("newTargetHost").value.trim();
    if (!host) { toast("warning", "请填写主机 IP 或域名"); return; }
    try {
      const r = await apiFetch("/api/targets/add", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, host }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || j.detail || "请求失败");
      if (j.ok) {
        toast("info", `${j.msg}（可达性：${j.reachable == null ? "等待采样" : j.reachable ? "可达" : "无ICMP响应"}）`);
        $("newTargetName").value = ""; $("newTargetHost").value = "";
        await refreshTargets(); renderTargetList();
      } else {
        toast("warning", j.msg);
      }
    } catch (e) { toast("critical", "添加失败"); }
  });
  $("btnFollow").addEventListener("click", () => { follow = true; manualWindow = null; updateChart(); });
  $("mainMetric").addEventListener("change", (e) => {
    mainMetric = e.target.value;
    const titleMap = { latency: "延迟走势", loss: "丢包率走势", jitter: "抖动走势",
                       std: "标准差走势", cv: "CV 变异系数走势", mos: "MOS 语音质量走势" };
    const titleEl = document.querySelector("#panelLatency .panel-head .panel-title");
    if (titleEl) titleEl.textContent = "目标与路径（点击行/卡片聚焦曲线）";
    const chartTitleEl = document.querySelector("#panelLatency .chart-ctrl-line .panel-title");
    if (chartTitleEl) chartTitleEl.textContent = `主图：${titleMap[mainMetric]} · 点击图例开关曲线 · 拖动底部条块缩放时间范围`;
    updateChart();
  });
  // 纵轴范围：选择结果存 localStorage，下次打开保持
  function syncHistViewControls() {
    if ($("axisMode")) $("axisMode").value = axisMode;
  }
  $("axisMode")?.addEventListener("change", (e) => {
    axisMode = e.target.value;
    try { localStorage.setItem("nm-axismode", axisMode); } catch (err) { /* 忽略 */ }
    updateChart();
  });
  $('viewWindow')?.addEventListener('change',(event)=>{
    viewSpanMs=Number(event.target.value)*60000;follow=true;manualWindow=null;
    const required=Math.ceil(viewSpanMs/3600000);
    if (required>Number($('historyHours').value)) {$('historyHours').value=required<=24?'24':'168';$('historyHours').dispatchEvent(new Event('change'));}
    updateChart();
  });
  window.addEventListener('nm-display-change',(event)=>{
    const prefs=event.detail || {};
    cur.displayPrefs=prefs;
    targets.forEach((target,index)=>{const color=prefs.targetColors?.[target.host];colorOf[target.host]=/^#[\da-f]{6}$/i.test(color||'')?color:PALETTE[index%PALETTE.length];});
    updateChart();updateBars();
  });
  $('btnPreviewAnalysis')?.addEventListener('click',async()=>{
    const button=$('btnPreviewAnalysis');button.disabled=true;
    try {const data=await readJSON('/api/analysis-preview?hours='+$('aiScope').value);$('analysisPreviewText').textContent=data.prompt;$('analysisPreview').open=true;}
    catch(error){toast('warning',error.message);}finally{button.disabled=false;}
  });
  $("btnSound").addEventListener("click", () => setSound(!soundOn));

  $("btnAIAnalyze").addEventListener("click", async () => {
    const hours = parseInt($("aiScope").value, 10) || 1;
    const el = $("aiResult");
    el.className = "ai-result loading";
    el.textContent = "正在调用 Qwen 分析…";
    const btn = $("btnAIAnalyze");
    btn.disabled = true;
    try {
      const r = await apiFetch("/api/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hours }), timeoutMs: 60000,
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || j.detail || "请求失败");
      if (j.error) {
        el.className = "ai-result error";
        el.textContent = "分析失败：" + j.error + (j.hint ? "\n\n" + j.hint : "");
      } else {
        el.className = "ai-result";
        el.textContent = j.analysis || "(无内容)";
      }
    } catch (e) {
      el.className = "ai-result error";
      el.textContent = "请求失败：" + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  let replayInFlight = null;
  async function syncReplay() {
    if (replayInFlight) return replayInFlight;
    replayInFlight = (async () => {
      let cursor = 0, session = cur.sessionId || "";
      for (let page = 0; page < 30; page++) {
        const response = await readJSON("/api/replay?" + new URLSearchParams({session_id:session,after:cursor,limit:5000}));
        if (!Array.isArray(response.rows)) break;
        if(cur.expectedSession && response.session_id!==cur.expectedSession)break;
        session = response.session_id; cur.sessionId = session;
        const grouped = {};
        const active = new Map(targets.map(t=>[t.target_id,t]));
        for (const item of response.rows) {
          if(item.type!=='ping'||(item.time_epoch||0)<(cur.timeEpoch||0))continue;
          const identities=targets.filter(t=>t.host===item.host).map(t=>t.target_id).filter(Boolean);
          if(identities.length && !identities.includes(item.target_id))continue;
          if(!targets.some(t=>t.host===item.host))continue;
          (grouped[item.host] ||= []).push(item);
        }
        for (const [host, rows] of Object.entries(grouped)) {
          const existing = ping[host];
          const values = new Map();
          if (existing) existing.t.forEach((ts,i) => values.set(ts,{ts,type:"ping",host,avg:existing.v[i],loss:existing.loss[i],jitter:existing.jitter[i],std:existing.std[i],state:existing.state?.[i]}));
          for (const item of rows) values.set(item.ts,item);
          const sorted = [...values.values()].sort((a,b)=>a.ts-b.ts).filter(i=>i.ts>=Date.now()/1000-600).slice(-REAL_KEEP);
          ping[host]={t:sorted.map(i=>i.ts),v:sorted.map(i=>i.avg),loss:sorted.map(i=>i.loss),jitter:sorted.map(i=>i.jitter),std:sorted.map(i=>i.std),state:sorted.map(i=>i.state),
            lossT:sorted.filter(i=>i.avg==null&&(i.state==null||i.state==="sent_timeout")).map(i=>i.ts),last:sorted.at(-1)};
        }
        cursor=response.next_sequence;
        if (!response.has_more) break;
      }
      aggregateQuality(Date.now()/1000); updateChart();
    })().catch(error=>console.warn("原始样本同步失败",error.message)).finally(()=>{replayInFlight=null;});
    return replayInFlight;
  }

  function restoreSnapshot(ov) {
    cur.expectedInterval = ov.expected_interval || 1;
    cur.thresholds = ov.quality_thresholds || cur.thresholds;
    const groups = ov.latest_by_target || {};
    const dispatch = { ping: onPing, tcp: onTcp, dns: onDns, traffic: onTraffic };
    for (const [kind, handler] of Object.entries(dispatch)) {
      const items = groups[kind] ? Object.values(groups[kind]) : [ov.latest && ov.latest[kind]].filter(Boolean);
      for (const item of items) handler({ ...item, type: kind });
    }
    updateOverall(); refreshTableSummaries(); updateChart(); updateBars();
  }

  // ---------- 初始化 ----------
  function initCharts() {
    // 关闭所有图表的入场/更新动画，避免每秒重绘触发动画
    const silent = { animation: false, animationDuration: 0, animationDurationUpdate: 0 };
    Object.values(charts).forEach((c) => { try { c.setOption(silent); } catch (e) { /* 忽略 */ } });
    // 不依赖数据，先把 axes 画出来；后续 update 覆盖。
    updateChart();
    updateBars();
    charts.histLat.setOption({ ...silent, grid: baseGrid, xAxis: baseAxis(), yAxis: yValue("ms"), series: [] });
    charts.histLoss.setOption({ ...silent, grid: baseGrid, xAxis: baseAxis(), yAxis: yValue("%"), series: [] });
    // ResizeObserver：主图所在的 .chart-wrap 高度/宽度变化时主动调用 ECharts.resize()
    // CSS Grid 让 .chart-main 用 flex:1 跟随表格区域高度，但 ECharts 不会自动感知，
    // 需要在容器尺寸变化时手动触发 chart.resize()，否则高度变化图表不跟随。
    if (window.ResizeObserver) {
      const targets = [
        { el: document.querySelector(".chart-wrap"), chart: charts.latency },
        { el: document.getElementById("chartLatency"), chart: charts.latency },
        { el: document.getElementById("chartHistLat"), chart: charts.histLat },
        { el: document.getElementById("chartHistLoss"), chart: charts.histLoss },
      ];
      targets.forEach(({ el, chart }) => {
        if (!el) return;
        let raf = 0;
        new ResizeObserver(() => {
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(() => { try { chart.resize(); } catch (e) { /* 忽略 */ } });
        }).observe(el);
      });
    }
    // dataZoom 联动 y 轴：用户拖动 slider 时按可见窗口重算 y 轴范围，
    // 解决"全时段有突出峰值 → 缩放后其他数据变化不明显"
    const seriesData = (c) => () => {
      try { return (c.getOption().series || []).map((s) => s.data || []); }
      catch (e) { return []; }
    };
    [charts.latency, charts.loss, charts.jitter, charts.tcp, charts.dns, charts.traffic]
      .forEach((c) => c && bindZoomY(c, seriesData(c)));
  }

  async function init() {
    renderMetricSelector();
    setSound(soundOn);
    applyMetricVisibility();
    syncHistViewControls();
    initCharts();
    try {
      const ov = await fetchOverview();
      if (!ov) return;
      applyOverview(ov);
      if(ov.status)onStatus(ov.status);
      window._start = Date.now() / 1000 - (ov.uptime || 0);
      setInterval(() => { $("uptime").textContent = fmtUptime(Date.now() / 1000 - window._start); }, 1000);
      cur.summary = ov.summary;
      cur.target_summaries = ov.target_summaries || {};
      restoreSnapshot(ov);
      updateOverall();
    } catch (e) { /* 忽略 */ }
    try {
      const ev = await readJSON("/api/events?limit=50");
      for (const [ts, type, message] of ev.events || []) addEvent(ts, LEVEL[type] || "info", message);
    } catch (e) { /* 忽略 */ }
    loadHistory();
    setInterval(loadHistory, 60000);

    // 每 15 秒拉 overview：更新 summary 和 target_summaries（按 IP 的 MOS/CV/可用率），并刷新表格列
    const pullOverview=async()=>{try{applyOverview(await fetchOverview());}catch(error){console.warn('状态同步失败',error.message);}};
    setInterval(pullOverview, 15000);
  }

  // ---------- WebSocket ----------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const token = document.querySelector('meta[name="nm-session-token"]')?.content || "";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    ws.onopen = async () => {
      $("statusText").textContent = "已连接，正在同步";
      const ov = await refreshTargets();
      if (ov) {
        cur.summary = ov.summary; cur.target_summaries = ov.target_summaries || {};
        if (ov.status) onStatus(ov.status);
        restoreSnapshot(ov);
        await syncReplay();
        refreshTableSummaries(); loadHistory();
      }
    };
    ws.onmessage = (e) => {
      let d;
      try { d = JSON.parse(e.data); } catch (error) { console.warn("忽略无效监测消息", error); return; }
      if (['ping','tcp','dns','traffic'].includes(d.type) && !snapshotGate.noteSample(d)) return;
      if (d.type === "ping") onPing(d);
      else if (d.type === "tcp") onTcp(d);
      else if (d.type === "dns") onDns(d);
      else if (d.type === "traffic") onTraffic(d);
      else if (d.type === "status") {
        onStatus(d.status);
        if (d.round_complete && Date.now()/1000-lastAggregateTs>30) {
          clearTimeout(aggregateTimer); lastAggregateTs=Date.now()/1000; aggregateQuality(lastAggregateTs);
          if (mainMetric === "cv" || mainMetric === "mos") updateChart();
        }
      }
      else if(d.type==='clock_changed'){acceptEpoch(d);lastHistSig='';loadHistory();}
      else if(d.type==='probes_changed'){for(const map of [tcpData,dnsData])for(const key of Object.keys(map))delete map[key];traffic.t=[];traffic.rx=[];traffic.tx=[];refreshTargets();updateBars();}
      else if (d.type === "alert") onAlert(d);
      else if (d.type === "trace") onTrace(d);
      else if (d.type === "traces_cleared") { traces = {}; $("pathInfo").textContent = "路径追踪已关闭"; refreshTargets(); }
    };
    ws.onclose = async (event) => {
      $("statusDot").className = "dot unknown";
      $("statusText").textContent = "已断开，重连中（数据已停止更新）";
      {
        $('statusText').textContent='会话更新，正在重新连接';
        try {
          const session=await readJSON('/api/session');
          if(session.token)document.querySelector('meta[name="nm-session-token"]').content=session.token;
        } catch(error){$('statusText').textContent='会话同步失败，稍后重试或刷新页面';}
      }
      setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
  }

  $("historyHours")?.addEventListener("change", () => { lastHistSig = ""; loadHistory(); });
  setInterval(() => {
    const receiptAge=((typeof performance!=='undefined'?performance.now():Date.now())-(cur.lastReceiveMono||0))/1000;
    const age = Math.max(receiptAge,Date.now() / 1000 - (cur.lastPingTs || 0));
    if (cur.lastPingTs && age > (cur.freshFor || 15)) {
      $("statusDot").className = "dot unknown";
      $("statusText").textContent = "采样已过期 · " + Math.floor(age) + " 秒未更新";
    }
  }, 2000);
  init();
  connect();
})();
