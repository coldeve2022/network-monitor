const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000}});
    const errors=[]; page.on('pageerror', error=>errors.push(error.message));
    if (process.env.NM_UI_SOURCE) await page.route('**/static/app.js', route => route.fulfill({contentType:'text/javascript',body:fs.readFileSync(process.env.NM_UI_SOURCE,'utf8')}));
    await page.goto('http://127.0.0.1:8768');
    await page.waitForFunction(() => echarts.getInstanceByDom(document.getElementById('chartLatency')).getOption().series?.some(s => s.data.length > 100));
    await page.locator('#btnFollow').click();
    const range = () => page.evaluate(() => {
      const chart=echarts.getInstanceByDom(document.getElementById('chartLatency'));
      const z=chart.getOption().dataZoom.find(z=>z.type==='slider');
      return {start:z.startValue,end:z.endValue,percent:[z.start,z.end]};
    });
    const handle = async (index) => page.evaluate(index => {
      const chart=echarts.getInstanceByDom(document.getElementById('chartLatency'));
      const v=chart._componentsViews.find(v=>v.__model.mainType==='dataZoom'&&v.__model.subType==='slider');
      const h=v._displayables.handles[index]; const r=h.getBoundingRect();
      const [x,y]=h.transformCoordToGlobal(r.x+r.width/2,r.y+r.height/2);
      const rect=chart.getDom().getBoundingClientRect(); return {x:rect.x+x,y:rect.y+y};
    },index);
    const initial=await range();
    const left=await handle(0);
    const box=await page.locator('#chartLatency').boundingBox();
    await page.mouse.move(left.x,left.y);
    await page.mouse.down();
    for(let step=1;step<=50;step++) {
      await page.mouse.move(left.x-(box.width*.45)*step/50,left.y);
      await page.waitForTimeout(30);
    }
    await page.mouse.up();
    const expanded=await range();
    console.log('slow handle drag',JSON.stringify({initial,expanded}));
    assert.ok(expanded.percent[0]>35 && expanded.percent[0]<70,'Handle must follow the drag distance rather than snapping to the full history');
    assert.ok(expanded.start<initial.start-3600000,'Left handle must expand the window by over one hour while real samples arrive');
    await page.waitForTimeout(2200);
    const retained=await range();
    assert.ok(Math.abs(retained.start-expanded.start)<100,'Live redraw must preserve manually selected start');
    assert.ok(Math.abs(retained.end-expanded.end)<100,'Live redraw must preserve manually selected end');
    const right=await handle(1);
    await page.mouse.move(right.x,right.y); await page.mouse.down();
    await page.mouse.move(right.x-box.width*.1,right.y,{steps:20}); await page.mouse.up();
    // ECharts throttles slider actions; wait for the final mouse-up update.
    await page.waitForTimeout(150);
    const narrowed=await range();
    console.log('right handle',JSON.stringify(narrowed));
    assert.ok(expanded.percent[1]-narrowed.percent[1]>7 && expanded.percent[1]-narrowed.percent[1]<14,'Right handle must resize by the dragged distance');
    const filler=await page.evaluate(()=>{
      const chart=echarts.getInstanceByDom(document.getElementById('chartLatency'));
      const v=chart._componentsViews.find(v=>v.__model.mainType==='dataZoom'&&v.__model.subType==='slider');
      const f=v._displayables.filler; const r=f.getBoundingRect();
      const [x,y]=f.transformCoordToGlobal(r.x+r.width/2,r.y+r.height/2);
      const rect=chart.getDom().getBoundingClientRect(); return {x:rect.x+x,y:rect.y+y};
    });
    await page.mouse.move(filler.x,filler.y); await page.mouse.down();
    for(let step=1;step<=40;step++) {await page.mouse.move(filler.x-box.width*.08*step/40,filler.y);await page.waitForTimeout(30);}
    await page.mouse.up();
    await page.waitForTimeout(150);
    const panned=await range();
    console.log('body pan',JSON.stringify(panned));
    assert.ok(narrowed.percent[0]-panned.percent[0]>5 && narrowed.percent[0]-panned.percent[0]<12,'Selected window must pan by the dragged distance');
    assert.ok(Math.abs((panned.end-panned.start)-(narrowed.end-narrowed.start))<100,'Panning must preserve the window width');
    await page.waitForTimeout(2200);
    const settled=await range();
    assert.ok(Math.abs(settled.start-panned.start)<100 && Math.abs(settled.end-panned.end)<100,'Manual pan must remain fixed during live refresh');
    await page.locator('#chartLatency').screenshot({path:'D:/codex work/network-monitor-qa/slider-fixed.png'});
    await page.locator('#btnFollow').click();
    const followed=await range();
    assert.ok(followed.end-followed.start>590000 && followed.end-followed.start<605000,'Follow button must restore ten minutes');
    await page.waitForTimeout(1500);
    const advancing=await range();
    assert.ok(advancing.start>followed.start,'Follow must advance with real samples');
    assert.deepEqual(errors,[]);
    console.log('PASS both handles, slow body pan, live retention and follow restoration');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
