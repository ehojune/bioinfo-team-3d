// Optional real-layout check. Uses an installed Chrome; no npm or browser download.
const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {spawn}=require('node:child_process');
const chromePath=process.env.LABHQ_TEST_CHROME || ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome','/usr/bin/chromium'].find(p=>fs.existsSync(p));
const web=process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname,'../labhq/web');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
test('real phone layout shows CSO, analyst, engineer and QC above the composer; reduced motion and local assets',
  {skip:!chromePath || typeof WebSocket==='undefined',timeout:90000},async()=>{
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'labhq-office-browser-'));
  const server=http.createServer((req,res)=>{
    const filename=path.resolve(web,decodeURIComponent(new URL(req.url,'http://localhost').pathname.slice(1)));
    if(!filename.startsWith(web+path.sep)){res.writeHead(403).end();return;}
    try {res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(filename)]||'application/octet-stream');res.end(fs.readFileSync(filename));}
    catch {res.writeHead(404).end();}
  });
  let chrome,ws;
  try {
    await new Promise(r=>server.listen(0,'127.0.0.1',r));
    const port=server.address().port;assert.ok(![8787,55921].includes(port));
    const origin=`http://127.0.0.1:${port}`;
    chrome=spawn(chromePath,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,
      '--no-first-run','--no-default-browser-check',...(process.env.CI?['--no-sandbox']:[]),'about:blank'],{windowsHide:true,stdio:'ignore'});
    let debug;
    for(let i=0;i<100;i++){try{debug=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0];break;}catch{await sleep(100);}}
    assert.ok(debug,'Chrome started');
    const targets=await(await fetch(`http://127.0.0.1:${debug}/json`)).json();
    ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
    await new Promise(r=>ws.onopen=r);
    let serial=0;const pending=new Map(),external=[],errors=[];
    ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Network.requestWillBeSent'&&/^https?:/.test(m.params.request.url)&&!m.params.request.url.startsWith(origin+'/'))external.push(m.params.request.url);
      if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);
      if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}};
    const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
    const evaluate=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    await cdp('Page.enable');await cdp('Network.enable');await cdp('Runtime.enable');
    await cdp('Page.addScriptToEvaluateOnNewDocument',{source:"localStorage.clear();const t=setTimeout;window.setTimeout=(f,ms,...a)=>ms===15000?0:t(f,ms,...a);"});
    const evidence=[];
    for(const [width,height]of [[1440,900],[390,844]]){
      await cdp('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
      await cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
      await cdp('Page.navigate',{url:origin+'/index.html?demo=1'});
      let ready=false;
      for(let i=0;i<250;i++){if(await evaluate("document.querySelectorAll('#approvals .ap').length>=2")){ready=true;break;}await sleep(100);}
      assert.ok(ready,'demo decisions loaded');await sleep(2800);
      if(width===390)await evaluate("document.querySelector('#office').scrollIntoView({block:'start'})");
      const result=await evaluate(`(()=>{const r=e=>{const b=e.getBoundingClientRect();return {top:b.top,bottom:b.bottom,width:b.width,height:b.height}};return {
        overflow:document.documentElement.scrollWidth>innerWidth,
        top:r(document.querySelector('.top')).bottom,
        bottom:Math.min(r(document.querySelector('#office')).bottom,r(document.querySelector('.staff-wrap')).top,r(document.querySelector('.composer')).top),
        core:['cso','analyst','engineer','qc_reviewer'].map(id=>({id,...r(document.querySelector('#zones [data-agent="'+id+'"]'))})),
        art:[...document.querySelectorAll('#zones .office-character')].map(r),
        animations:document.querySelector('#office').getAnimations({subtree:true}).length,
        decision:[...document.querySelectorAll('#zones [data-status="decision"]')].map(e=>e.textContent)}})()`);
      assert.equal(result.overflow,false);assert.equal(result.animations,0);
      assert.equal(result.art.length,11,'full roster artwork');
      assert.ok(result.art.every(r=>r.width>=64&&r.height>=64));
      assert.ok(result.decision.some(t=>t.includes('승인 기다려요')));
      for(const seat of result.core){assert.ok(seat.top>=result.top-1,JSON.stringify({width,seat,result}));assert.ok(seat.bottom<=result.bottom+1,JSON.stringify({width,seat,result}));}
      const contrast=[];
      const lum=rgb=>{const c=rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722;};
      for(const theme of ['light','dark']){
        await evaluate(`document.documentElement.dataset.theme='${theme}'`);
        const pairs=await evaluate(`(()=>{return [...document.querySelectorAll('.office .nm,.office .sub,.office-status,.office .zone h3')].map(e=>{let p=e,bg;while(p){bg=getComputedStyle(p).backgroundColor;if(bg!=='rgba(0, 0, 0, 0)')break;p=p.parentElement;}return {color:getComputedStyle(e).color,bg};})})()`);
        for(const pair of pairs){const a=lum(pair.color),b=lum(pair.bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);assert.ok(ratio>=4.5,JSON.stringify({theme,pair,ratio}));contrast.push(ratio);}
      }
      result.minTextContrast=Math.min(...contrast);
      await evaluate("document.documentElement.dataset.theme='light'");
      await cdp('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]});
      assert.ok(await evaluate("document.querySelector('#office').getAnimations({subtree:true}).length>0"));
      evidence.push({width,height,...result});
    }
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    console.log(JSON.stringify({office:evidence,externalRequests:external.length}));
    await cdp('Browser.close');
  } finally {
    ws?.close();chrome?.kill();server.close();
    // Only our freshly created temporary Chrome profile.
    for(let i=0;i<20;i++){try{fs.rmSync(profile,{recursive:true,force:true});break;}catch(e){if(i===19)throw e;await sleep(100);}}
  }
});
