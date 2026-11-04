(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const columnLabels = {current:'当前延迟',avg:'平均延迟',loss:'窗口丢包',jitter:'抖动',mos:'MOS估算',cv:'CV',availability:'响应率'};
  const allColumns = Object.keys(columnLabels);
  const defaults = { version:2,density: 'comfortable', font: '16', layout: 'stacked', chartHeight: '320', accent: '#4da3ff', cards: false,tableWidth:40,columns:['current','avg','loss','jitter'],columnOrder:allColumns,targetOrder:[],targetColors:{} };
  const choices = { density: ['comfortable', 'compact'], font: ['16', '18', '20'], layout: ['stacked', 'split'], chartHeight: ['320', '440', '560'] };
  let prefs = { ...defaults };
  try { Object.assign(prefs, JSON.parse(localStorage.getItem('nm-display') || '{}')); } catch (_) { /* Defaults remain available. */ }
  for (const [key, options] of Object.entries(choices)) if (!options.includes(prefs[key])) prefs[key] = defaults[key];
  if (!/^#[\da-f]{6}$/i.test(prefs.accent)) prefs.accent = defaults.accent;
  prefs.cards = prefs.cards === true;
  prefs.tableWidth=Math.max(25,Math.min(65,Number(prefs.tableWidth)||40));
  prefs.columns=Array.isArray(prefs.columns)?prefs.columns.filter(key=>allColumns.includes(key)):defaults.columns;
  prefs.columnOrder=[...new Set([...(Array.isArray(prefs.columnOrder)?prefs.columnOrder:[]),...allColumns])].filter(key=>allColumns.includes(key));
  prefs.targetOrder=Array.isArray(prefs.targetOrder)?prefs.targetOrder.filter(key=>typeof key==='string').slice(0,200):[];
  prefs.targetColors=prefs.targetColors&&typeof prefs.targetColors==='object'?Object.fromEntries(Object.entries(prefs.targetColors).filter(([k,v])=>/^#[\da-f]{6}$/i.test(v)).slice(0,200)):{};

  function applyColumns() {
    const style=$('nmColumnStyle') || document.head.appendChild(Object.assign(document.createElement('style'),{id:'nmColumnStyle'}));
    style.textContent=allColumns.filter(key=>!prefs.columns.includes(key)).map(key=>`#targetTable [data-column="${key}"]{display:none}`).join('\n');
    const order=['target',...prefs.columnOrder];
    document.querySelectorAll('#targetTable tr').forEach(row=>{
      for(const key of order){const cell=row.querySelector(`[data-column="${key}"]`);if(cell)row.appendChild(cell);}
    });
    const rows=[...document.querySelectorAll('#targetTable tbody tr')];
    const rank=key=>{const index=prefs.targetOrder.indexOf(key);return index<0?10000:index;};
    rows.sort((a,b)=>rank(a.dataset.targetKey)-rank(b.dataset.targetKey)).forEach(row=>row.parentElement.appendChild(row));
    const cards=[...document.querySelectorAll('#targetCards .tcard')];
    cards.sort((a,b)=>rank(a.dataset.targetKey)-rank(b.dataset.targetKey)).forEach(card=>card.parentElement.appendChild(card));
  }
  function renderColumns() {
    const wrap=$('columnOptions');wrap.replaceChildren(Object.assign(document.createElement('legend'),{textContent:'表格列（可调整顺序）'}));
    for(const key of prefs.columnOrder){
      const row=document.createElement('div');row.className='column-option';
      const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.dataset.col=key;check.checked=prefs.columns.includes(key);
      check.addEventListener('change',()=>{prefs.columns=check.checked?[...prefs.columns,key]:prefs.columns.filter(k=>k!==key);save();});
      label.append(check,document.createTextNode(columnLabels[key]));row.append(label);
      for(const [direction,text] of [[-1,'↑'],[1,'↓']]){
        const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=text;button.setAttribute('aria-label',columnLabels[key]+(direction<0?'前移':'后移'));
        button.addEventListener('click',()=>{const index=prefs.columnOrder.indexOf(key),next=index+direction;if(next>=0&&next<prefs.columnOrder.length){[prefs.columnOrder[index],prefs.columnOrder[next]]=[prefs.columnOrder[next],prefs.columnOrder[index]];save();}});row.append(button);
      }wrap.append(row);
    }
  }
  function renderTargets() {
    const wrap=$('targetDisplayOptions');wrap.replaceChildren();
    const rows=[...document.querySelectorAll('#targetTable tbody tr')];
    for(const item of rows){
      const row=document.createElement('div');row.className='target-display-row';
      const name=document.createElement('span');name.textContent=item.querySelector('td')?.textContent || item.dataset.host;name.title=name.textContent;
      const color=document.createElement('input');color.type='color';color.value=prefs.targetColors[item.dataset.host]||'#4da3ff';color.setAttribute('aria-label',name.textContent+'曲线颜色');
      color.addEventListener('change',()=>{prefs.targetColors[item.dataset.host]=color.value;save();});row.append(name,color);
      for(const [direction,text] of [[-1,'↑'],[1,'↓']]){
        const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=text;button.setAttribute('aria-label',name.textContent+(direction<0?'上移':'下移'));
        button.addEventListener('click',()=>{const order=rows.map(r=>r.dataset.targetKey);const index=order.indexOf(item.dataset.targetKey),next=index+direction;if(next>=0&&next<order.length){[order[index],order[next]]=[order[next],order[index]];prefs.targetOrder=order;save();}});row.append(button);
      }wrap.append(row);
    }
  }

  function apply() {
    const root = document.documentElement;
    root.dataset.density = prefs.density;
    root.dataset.layout = prefs.layout;
    root.dataset.cards = String(prefs.cards);
    root.style.setProperty('--ui-font-size', prefs.font + 'px');
    root.style.setProperty('--chart-height', prefs.chartHeight + 'px');
    root.style.setProperty('--accent', prefs.accent);
    root.style.setProperty('--table-width',prefs.tableWidth+'%');
    $('uiTableWidth').value=prefs.tableWidth;
    $('layoutDivider').setAttribute('aria-valuenow',String(prefs.tableWidth));
    for (const key of ['density', 'font', 'layout', 'chartHeight', 'accent']) {
      $('ui' + key[0].toUpperCase() + key.slice(1)).value = prefs[key];
    }
    $('uiCards').checked = prefs.cards;
    applyColumns();renderColumns();renderTargets();
    window.dispatchEvent(new CustomEvent('nm-display-change',{detail:prefs}));
    window.dispatchEvent(new Event('resize'));
  }
  function save() {
    try { localStorage.setItem('nm-display', JSON.stringify(prefs)); } catch (_) { /* Session changes still work. */ }
    apply();
  }
  for (const key of ['density', 'font', 'layout', 'chartHeight', 'accent', 'cards']) {
    $('ui' + key[0].toUpperCase() + key.slice(1)).addEventListener('change', (event) => {
      prefs[key] = key === 'cards' ? event.target.checked : event.target.value;
      save();
    });
  }
  $('resetDisplay').addEventListener('click', () => { prefs = { ...defaults }; save(); });
  $('uiTableWidth').addEventListener('input',event=>{prefs.tableWidth=Number(event.target.value);save();});
  const divider=$('layoutDivider');
  divider.addEventListener('pointerdown',event=>{divider.setPointerCapture(event.pointerId);divider.dataset.dragging='true';});
  divider.addEventListener('pointermove',event=>{if(divider.dataset.dragging!=='true')return;const rect=document.querySelector('.target-layout').getBoundingClientRect();prefs.tableWidth=Math.max(25,Math.min(65,(event.clientX-rect.left)/rect.width*100));document.documentElement.style.setProperty('--table-width',prefs.tableWidth+'%');});
  divider.addEventListener('pointerup',()=>{delete divider.dataset.dragging;save();});
  divider.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();prefs.tableWidth=Math.max(25,Math.min(65,prefs.tableWidth+(event.key==='ArrowLeft'?-1:1)));save();}});
  window.addEventListener('nm-targets-rendered',()=>{applyColumns();renderTargets();});
  function downloadJson(name,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  $('exportDisplay').addEventListener('click',()=>downloadJson('network-monitor-display.json',prefs));
  $('importDisplay').addEventListener('change',async event=>{try{const value=JSON.parse(await event.target.files[0].text());if(!value||typeof value!=='object')throw Error('无效界面方案');localStorage.setItem('nm-display',JSON.stringify(value));location.reload();}catch(error){alert('导入失败：'+error.message);}});
  apply();

  function updateExport() {
    const hours = $('historyHours').value;
    $('exportHistory').href = '/api/export?' + new URLSearchParams({ metric: $('exportMetric').value, hours });
    const label = hours === '168' ? '7 天' : hours === '720' ? '30 天' : hours + ' 小时';
    $('historyLatencyTitle').textContent = label + '延迟';
    $('historyLossTitle').textContent = label + '丢包记录';
    try { localStorage.setItem('nm-history-hours', hours); } catch (_) { /* Optional persistence. */ }
  }
  try {
    const saved = localStorage.getItem('nm-history-hours');
    if (['1', '6', '24', '168', '720'].includes(saved)) $('historyHours').value = saved;
  } catch (_) { /* Defaults remain. */ }
  $('historyHours').addEventListener('change', updateExport);
  $('exportMetric').addEventListener('change', updateExport);
  updateExport();
  $('historyHours').dispatchEvent(new Event('change'));

  function tcpRow(target={name:'',host:'',port:443}) {
    const row=document.createElement('div');row.className='tcp-editor-row';
    for(const [key,label,type] of [['name','名称','text'],['host','主机','text'],['port','端口','number']]){const input=document.createElement('input');input.className='input';input.type=type;input.dataset.field=key;input.value=target[key];input.placeholder=label;input.setAttribute('aria-label','TCP目标'+label);if(key==='port'){input.min='1';input.max='65535';}row.append(input);}
    const remove=document.createElement('button');remove.type='button';remove.className='btn';remove.textContent='移除';remove.addEventListener('click',()=>row.remove());row.append(remove);$('tcpTargetEditor').append(row);
  }
  window.addEventListener('nm-settings-open',async()=>{
    try{const response=await fetch('/api/probes',{signal:AbortSignal.timeout(8000)});if(!response.ok)throw Error('无法读取专项目标');const data=await response.json(),settings=data.settings||{};
      $('tcpTargetEditor').replaceChildren();for(const target of settings.tcp_targets||[])tcpRow(target);
      $('dnsServers').value=(settings.dns_servers||[]).join('\n');$('dnsQueryHost').value=settings.dns_query_host||'';$('traceTargets').value=(settings.trace_targets||[]).join('\n');
      $('trafficInterface').replaceChildren(new Option('系统总计（全部网卡）',''));for(const name of data.interfaces||[])$('trafficInterface').append(new Option(name,name));$('trafficInterface').value=settings.traffic_interface||'';
      $('probeSettingsError').textContent='';
    }catch(error){$('probeSettingsError').textContent=error.message;}
  });
  $('addTcpTargetRow').addEventListener('click',()=>tcpRow());
  $('saveProbeSettings').addEventListener('click',async()=>{
    const button=$('saveProbeSettings');button.disabled=true;
    try{const settings={tcp_targets:[...document.querySelectorAll('.tcp-editor-row')].map(row=>Object.fromEntries([...row.querySelectorAll('input')].map(input=>[input.dataset.field,input.dataset.field==='port'?Number(input.value):input.value.trim()]))),dns_servers:$('dnsServers').value.split(/\s+/).filter(Boolean),dns_query_host:$('dnsQueryHost').value.trim(),trace_targets:$('traceTargets').value.split(/\s+/).filter(Boolean),traffic_interface:$('trafficInterface').value};
      const token=document.querySelector('meta[name="nm-session-token"]')?.content||'';
      const response=await fetch('/api/probes',{method:'POST',headers:{'Content-Type':'application/json','X-NM-Token':token},body:JSON.stringify(settings),signal:AbortSignal.timeout(10000)});const data=await response.json();if(!response.ok)throw Error(data.detail||'保存失败');$('probeSettingsError').textContent='已保存，下一轮探测生效';
    }catch(error){$('probeSettingsError').textContent=error.message;}finally{button.disabled=false;}
  });

  $('btnDiagnose').addEventListener('click', async () => {
    const button = $('btnDiagnose');
    const result = $('diagnosticResult');
    button.disabled = true;
    result.textContent = '正在检查当前数据…';
    try {
      const response = await fetch('/api/diagnostics');
      if (!response.ok) throw new Error('读取诊断失败，请确认程序仍在运行');
      const data = await response.json();
      result.replaceChildren();
      result.dataset.level = data.level;
      const heading = document.createElement('h3'); heading.textContent = data.title;
      const timestamp = document.createElement('p'); timestamp.className = 'hint'; timestamp.textContent = '检查时间：' + new Date(data.ts * 1000).toLocaleString('zh-CN');
      const list = document.createElement('ul');
      for (const line of [...data.findings, ...data.suggestions]) {
        const item = document.createElement('li'); item.textContent = line; list.appendChild(item);
      }
      result.append(heading, timestamp, list);
    } catch (error) { result.textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
