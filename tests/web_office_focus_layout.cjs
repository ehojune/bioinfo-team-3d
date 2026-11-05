const {test} = require('node:test');
const assert = require('node:assert/strict');
const {openDemo, skipReason} = require('./support/demo_driver.cjs');
// Browser phase times on stderr only: where a run spends its 30 s after a cleanup failure (#842).
const trace = name => event => console.error('focus driver: ' + JSON.stringify({test: name, ...event}));

test('office prioritizes decisions, exposes plan progress and opens failed results',
  {skip:skipReason() || false, timeout:120000}, async t => {
    const d = await openDemo({width:1440,height:900, diagnostic:trace('priorities')});
    try {
      await d.load();
      await d.until('running', "[...__labhq.S.activeTasks.values()].some(t=>t.agent_id==='biologist')");
      await t.test('step flow stays above decisions and marks the running step', async () => {
        const flow = await d.evaluate(`(()=>{const f=document.querySelector('.step-flow');return f && {
          visible:f.getBoundingClientRect().height>0, current:f.querySelector('[aria-current="step"]')?.textContent,
          outside:!f.closest('[data-tab]'), before:f.getBoundingClientRect().bottom<=document.querySelector('#cc-tabs').getBoundingClientRect().top};})()`);
        assert.ok(flow?.visible && flow.outside && flow.before);
        assert.match(flow.current,/s1.*곰.*진행 중/);
        assert.equal(await d.evaluate("document.querySelector('[data-tab-target=decisions]').getAttribute('aria-selected')"),'true');
      });
      await t.test('default decision panel takes at least half the desktop and exceeds office height', async () => {
        const sizes = await d.evaluate(`(()=>{const c=document.querySelector('#command-center').getBoundingClientRect(),o=document.querySelector('#office').getBoundingClientRect();return {w:c.width,h:c.height,oh:o.height};})()`);
        assert.ok(sizes.w>=720,JSON.stringify(sizes)); assert.ok(sizes.h>sizes.oh,JSON.stringify(sizes));
      });
      await t.test('staff summaries are no taller than 72px and open their details', async () => {
        assert.ok(await d.evaluate("[...document.querySelectorAll('#staff-strip .staff-card')].every(c=>c.getBoundingClientRect().height<=72)"));
        await d.evaluate("document.querySelector('#staff-strip .staff-card').click()");
        assert.equal(await d.evaluate("document.querySelector('#sheet').open"),true);
        await d.evaluate("document.querySelector('#sheet').close()");
      });
      await t.test('closing staff returns space to decisions and persists through reload', async () => {
        const h=await d.evaluate("document.querySelector('#command-center').getBoundingClientRect().height");
        await d.evaluate("document.querySelector('#staff-close').click()"); await d.frames();
        assert.ok(await d.evaluate(`document.querySelector('#command-center').getBoundingClientRect().height>${h}+50`));
        assert.equal(await d.evaluate("JSON.parse(localStorage.getItem('labhq_demo:panel_sizes:')).staffClosed"),true);
        // The demo driver clears storage on each navigation: restore only this instance's saved settings.
        const saved=await d.evaluate("JSON.stringify({...localStorage})");
        const script=await d.cdp('Page.addScriptToEvaluateOnNewDocument',{source:`for(const [k,v] of Object.entries(${saved}))localStorage.setItem(k,v);`});
        await d.load();
        assert.equal(await d.evaluate("document.querySelector('.staff-wrap').hidden"),true);
        await d.evaluate("document.querySelector('#staff-reopen').click()"); await d.frames();
        assert.equal(await d.evaluate("document.querySelector('.staff-wrap').hidden"),false);
        assert.equal(await d.evaluate("Boolean(JSON.parse(localStorage.getItem('labhq_demo:panel_sizes:')).staffClosed)"),false);
        await d.cdp('Page.removeScriptToEvaluateOnNewDocument',{identifier:script.identifier});
      });
      await t.test('failed result button shows reason, remaining results and next action immediately', async () => {
        await d.evaluate(`(()=>{const id='failed-layout';__labhq.apply({type:'request.created',request_id:id,data:{text:'실패 결과 확인'}});
          __labhq.apply({type:'request.plan',request_id:id,data:{steps:Array.from({length:12},(_,i)=>({id:'s'+i,agent_id:'biologist',instruction:'단계 '+i}))}});
          __labhq.apply({type:'request.failed',request_id:id,data:{ok:false,pi_review_pending:true,error:'입력 파일을 찾지 못했습니다',report:'확보한 결과: 문헌 목록'}});})()`);
        await d.frames();
        for(const selector of ['#review-cards [data-review-request="failed-layout"] button','#board [data-open-review="failed-layout"]']) {
          await d.evaluate("document.querySelector('[data-tab-target=decisions]').click()");
          await d.evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); await d.frames();
          const shown=await d.evaluate(`(()=>{const e=document.querySelector('#request-outcome'),c=document.querySelector('#command-center');if(!e)return null;const r=e.getBoundingClientRect(),b=c.getBoundingClientRect();return {text:e.innerText,visible:r.top>=b.top && r.top<Math.min(b.bottom,innerHeight),focused:e.contains(document.activeElement)};})()`);
          assert.match(shown?.text || '',/입력 파일을 찾지 못했습니다/);
          assert.match(shown.text,/확보한 결과: 문헌 목록/); assert.match(shown.text,/다음 행동/);
          assert.ok(shown.visible && shown.focused,JSON.stringify(shown));
        }
      });
      assert.deepEqual(d.errors,[]); assert.deepEqual(d.external,[]);
    } finally {await d.close();}
  });

test('layout version migration, phone header and instance-specific staff visibility',
  {skip:skipReason() || false, timeout:60000}, async () => {
    const d=await openDemo({width:1440,height:900, diagnostic:trace('migration')});
    try {
      await d.cdp('Page.addScriptToEvaluateOnNewDocument',{source:`window.LABHQ_BOOT={mode:'demo',instance:'layout-a'};
        localStorage.setItem('labhq_demo:panel_sizes:layout-a','{"main":900,"staff":260}');
        localStorage.setItem('labhq_demo:panel_sizes:layout-b','{"main":800,"staffClosed":true}');`});
      await d.load();
      assert.ok(await d.evaluate("document.querySelector('#command-center').getBoundingClientRect().width>=720"));
      assert.equal(await d.evaluate("document.querySelector('.staff-wrap').getBoundingClientRect().height"),92);
      await d.evaluate("document.querySelector('#staff-close').click()");
      assert.equal(await d.evaluate("localStorage.getItem('labhq_demo:panel_sizes:layout-b')"),'{"main":800,"staffClosed":true}');
      await d.cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
      await d.evaluate("document.querySelector('#staff-reopen').click()");
      await d.until('running', "[...__labhq.S.activeTasks.values()].some(t=>t.agent_id==='biologist')");
      await d.evaluate('window.scrollTo(0,0)'); await d.frames();
      const header=await d.evaluate(`(()=>{const h=document.querySelector('.cc-heading'),f=document.querySelector('.step-flow'),c=document.querySelector('#command-center');return {top:h.getBoundingClientRect().top,cc:c.getBoundingClientRect().top,scroll:f.scrollWidth>f.clientWidth,overflow:document.documentElement.scrollWidth>innerWidth};})()`);
      assert.ok(Math.abs(header.top-header.cc)<20); assert.ok(header.scroll); assert.equal(header.overflow,false);
      assert.equal(await d.evaluate("document.querySelector('.staff-wrap').hidden"),false);
      assert.ok(await d.evaluate("document.querySelector('#staff-strip .staff-card').getBoundingClientRect().height<=72"));
      await d.evaluate(`(()=>{const q=__labhq.S.requests.get(__labhq.S.current);q.plan.slice(0,4).forEach(s=>q.steps[s.id]='done');q.steps[q.plan[4].id]='working';__labhq.apply({type:'ping'});})()`);
      await d.frames();
      assert.match(await d.evaluate("document.querySelector('.step-flow').textContent"),/✓ 4/);
      assert.match(await d.evaluate("document.querySelector('.step-flow [aria-current=step]').textContent"),/s5/);
      await d.evaluate("__labhq.S.requests.get(__labhq.S.current).plan=[];__labhq.apply({type:'ping'})"); await d.frames();
      assert.equal(await d.evaluate("document.querySelector('.step-flow').hidden"),true);
      assert.deepEqual(d.errors,[]);
    } finally {await d.close();}
  });
