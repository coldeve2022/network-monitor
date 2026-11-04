const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});const failures=[];
try{const page=await browser.newPage({viewport:{width:390,height:844}});page.setDefaultTimeout(4000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.WebSocket=class{constructor(){window.__socket=this;}}});
 const root=path.join(__dirname,'../web/static');const now=Date.now()/1000;
 const targets=Array.from({length:9},(_,i)=>({host:'192.0.2.'+(i+1),name:i?'业务目标'+i:'网关',kind:'base',target_id:'target'+i,protected:i===0}));
 const latest=Object.fromEntries(targets.map((t,i)=>[t.host,{...t,ts:now,type:'ping',avg:10+i*10,loss:0,std:1,jitter:1,state:'ok'}]));
 const summaries=Object.fromEntries(targets.map((t,i)=>[t.host,{latency_avg:10+i*10,loss_avg:0,jitter_avg:1,std_avg:1,mos:4.4,cv:10,availability:100}]));
 const schema={ping_interval:{label:'Ping采样频率',min:.5,max:60,step:.5,unit:'秒'},ping_timeout:{label:'Ping超时',min:.5,max:10,step:.5,unit:'秒'},stat_window:{label:'统计窗口',min:5,max:600,step:5,unit:'轮'},enable_traceroute:{label:'路径追踪',type:'bool'}};
 await page.route('**/*',async route=>{const u=new URL(route.request().url());if(u.pathname.startsWith('/api/')){
 const data=u.pathname==='/api/overview'?{targets,latest_by_target:{ping:latest},traces:{},status:'good',uptime:3600,db_size:8,expected_interval:1,probe_health:{ping:{fresh_for:15}},target_summaries:summaries}:
 u.pathname==='/api/config'?{schema,settings:{ping_interval:1,ping_timeout:2,stat_window:60,enable_traceroute:false}}:
 u.pathname==='/api/probes'?{settings:{tcp_targets:[{name:'业务服务',host:'example.test',port:443}],dns_servers:['192.0.2.53'],dns_query_host:'example.test',trace_targets:['192.0.2.1'],traffic_interface:''},interfaces:['Ethernet','Wi-Fi']}:
 u.pathname==='/api/history'?{rows:targets.flatMap((t,i)=>Array.from({length:30},(_,j)=>[Math.floor(now/60)*60-(30-j)*60,t.host,10+i*10,8,20,0,1,1]))}:
 u.pathname==='/api/replay'?{rows:[],has_more:false,session_id:'fixture',next_sequence:0}:u.pathname==='/api/events'?{events:[]}:{ok:true};return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});}
 const file=u.pathname==='/'?'index.html':u.pathname.replace(/^\/static\//,'');return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,file))});});
 await page.goto('http://audit.local');await page.waitForFunction(()=>document.querySelectorAll('#targetTable tbody tr').length===9);
 async function check(name,fn){try{await fn();console.log('PASS '+name)}catch(e){failures.push(name+': '+e.message);console.log('FAIL '+name+': '+e.message)}}
 await check('mobile settings opens at title and first field',async()=>{await page.locator('#btnSettings').click();await page.locator('#settingsModal').waitFor({state:'visible'});assert.equal(await page.locator('.modal-box').evaluate(e=>e.scrollTop),0);await page.keyboard.press('Escape')});
 await page.keyboard.press('Escape');
 await check('validation is visible inside modal',async()=>{await page.locator('#btnSettings').click();await page.locator('#settingsModal').waitFor({state:'visible'});await page.locator('#setting-ping_interval').fill('999');await page.locator('#btnSave').click();assert.ok(await page.evaluate(()=>document.getElementById('settingsError')?.textContent.length>0));assert.ok(await page.evaluate(()=>Number(getComputedStyle(document.getElementById('toast')).zIndex)>Number(getComputedStyle(document.getElementById('settingsModal')).zIndex)));await page.keyboard.press('Escape')});
 await page.keyboard.press('Escape');
 await check('mobile component spacing applies',async()=>{const value=await page.locator('#panelLatency').evaluate(e=>parseFloat(getComputedStyle(e).paddingLeft));assert.ok(value<=14,'mobile padding '+value)});
 await check('accent applies to focus and chart controls',async()=>{await page.locator('#displayOptions').evaluate(e=>e.open=true);await page.locator('#uiAccent').fill('#ff6600');await page.locator('#uiAccent').dispatchEvent('change');const color=await page.evaluate(()=>echarts.getInstanceByDom(document.getElementById('chartLatency')).getOption().dataZoom[1].handleStyle.color);assert.equal(color,'#ff6600')});
 await check('table columns can be selected',async()=>{assert.ok(await page.locator('[data-col="mos"]').count());await page.locator('[data-col="mos"]').check();assert.notEqual(await page.locator('#targetTable .v-mos').first().evaluate(e=>getComputedStyle(e).display),'none')});
 await check('chart font follows chosen font size',async()=>{await page.locator('#uiFont').selectOption('20');const size=await page.evaluate(()=>echarts.getInstanceByDom(document.getElementById('chartLatency')).getOption().xAxis[0].axisLabel.fontSize);assert.ok(size>=14,'chart font '+size)});
 await page.locator('#displayOptions').evaluate(e=>e.open=false);
 await check('protocol targets have editable fields',async()=>{await page.locator('#btnSettings').click();await page.locator('#settingsModal').waitFor({state:'visible'});assert.ok(await page.locator('#tcpTargetEditor input').count()>=3);await page.keyboard.press('Escape')});
 const output=process.env.NM_QA_OUTPUT||path.join(__dirname,'../../ui-qa');fs.mkdirSync(output,{recursive:true});await page.keyboard.press('Escape');await page.locator('#toast').evaluate(e=>e.classList.add('hidden'));await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});await page.setViewportSize({width:1600,height:1000});await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({checks:7,failures,output}));if(failures.length)process.exitCode=1;
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
