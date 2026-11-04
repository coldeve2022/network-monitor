const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const failures=[];
 try {
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__now=Date.now();Date.now=()=>window.__now;window.WebSocket=class{constructor(){window.__socket=this;}};});
  const dir=path.join(__dirname,'../web/static');
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.pathname.startsWith('/api/')){const data=u.pathname==='/api/overview'?{targets:[{host:'a',name:'a',kind:'base'}],traces:{},status:'good',target_summaries:{},latest_by_target:{},trace_enabled:false,uptime:10,db_size:1}:u.pathname==='/api/history'?{rows:[]}:u.pathname==='/api/events'?{events:[]}:{schema:{},settings:{}};return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});}
   const filename=u.pathname==='/'?'index.html':path.basename(u.pathname);let body=fs.readFileSync(path.join(dir,filename));
   if(filename==='app.js')body=body.toString().replace(/  init\(\);\s*connect\(\);/,'globalThis.qa={run:code=>eval(code)}; init(); connect();');
   return route.fulfill({contentType:filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html',body});
  });
  await page.goto('http://audit.local');await page.waitForFunction(()=>window.qa&&document.querySelector('#targetTable tbody tr'));
  async function check(name,fn){try{await fn();console.log('PASS '+name);}catch(e){failures.push(name+': '+e.message);console.log('FAIL '+name+': '+e.message);}}
  await check('new successful peak is visible on the first redraw',async()=>{
   await page.evaluate(()=>qa.run(`hist.a={t:[Date.now()-3600000],v:[10],loss:[0],jitter:[0],std:[0]};for(let i=4;i>=0;i--)onPing({host:'a',name:'a',kind:'base',ts:Date.now()/1000-i,avg:10,loss:0,std:0,jitter:0});updateChart();`));
   await page.evaluate(()=>{window.__now+=1000;qa.run(`onPing({host:'a',name:'a',kind:'base',ts:Date.now()/1000,avg:1000,loss:0,std:0,jitter:990});`);});
   const y=await page.evaluate(()=>qa.run('charts.latency.getOption().yAxis[0]'));assert.ok(y.max>=1000,JSON.stringify({min:y.min,max:y.max}));
  });
  await check('right edge follows while preserving user span',async()=>{
   await page.evaluate(()=>qa.run(`charts.latency.dispatchAction({type:'dataZoom',start:90,end:100});`));
   const before=await page.evaluate(()=>qa.run('charts.latency.getOption().dataZoom[1]'));
   await page.evaluate(()=>{window.__now+=5000;qa.run(`onPing({host:'a',name:'a',kind:'base',ts:Date.now()/1000,avg:10,loss:0,std:0,jitter:0});`);});
   const after=await page.evaluate(()=>qa.run('charts.latency.getOption().dataZoom[1]'));
   assert.ok(after.endValue>before.endValue,'right boundary remained frozen');assert.ok(Math.abs((after.endValue-after.startValue)-(before.endValue-before.startValue))<10,'span changed');
  });
  await check('manual history window stays fixed',async()=>{
   await page.evaluate(()=>qa.run(`charts.latency.dispatchAction({type:'dataZoom',start:40,end:60});`));
   const b=await page.evaluate(()=>qa.run('charts.latency.getOption().dataZoom[1]'));
   await page.evaluate(()=>{window.__now+=5000;qa.run('updateChart()');});
   const a=await page.evaluate(()=>qa.run('charts.latency.getOption().dataZoom[1]'));assert.equal(a.startValue,b.startValue);assert.equal(a.endValue,b.endValue);
  });
  await check('missing quality series never falls back to RTT',async()=>{
   const d=await page.evaluate(()=>qa.run(`delete cvSeries.a;delete mosSeries.a;delete hist.a;mainMetric='mos';updateChart();charts.latency.getOption().series[0].data`));
   assert.ok(d.every(p=>p[1]==null||p[1]>=1&&p[1]<=5),'raw RTT used as MOS');
  });
  await check('quality aggregation is independent of target arrival order',async()=>{
   await page.evaluate(()=>qa.run(`targets=[{host:'a',name:'a',kind:'base'},{host:'b',name:'b',kind:'base'}];for(const map of [ping,mosSeries,cvSeries])for(const k of Object.keys(map))delete map[k];lastAggregateTs=0;`));
   for(let round=0;round<3;round++){
    await page.evaluate(()=>{window.__now+=60000;for(const host of ['a','b'])qa.run(`onPing({host:'${host}',name:'${host}',kind:'base',ts:Date.now()/1000,avg:20,loss:0,std:0,jitter:2})`);});
    await page.waitForTimeout(80);
   }
   const q=await page.evaluate(()=>qa.run('({a:mosSeries.a,b:mosSeries.b})'));assert.equal(q.a.v.at(-1),4.39);assert.equal(q.b.v.at(-1),4.39);
  });
  assert.deepEqual(errors,[]);console.log(JSON.stringify({checks:5,failures},null,2));if(failures.length)process.exitCode=1;
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
