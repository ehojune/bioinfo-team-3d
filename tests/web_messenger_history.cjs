const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const modulePath = path.join(root, 'ui/messenger.js');
const load = () => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(modulePath, 'utf8')).toString('base64'));
const event = seq => ({seq, type:'agent.log', ts:seq, agent_id:'worker', data:{text:'message '+seq}});

test('scrolling up prepends older messages, preserves position and shows beginning', async () => {
  const {createMessenger, bindHistoryViewport} = await load();
  const calls=[];
  let finish;
  const history=createMessenger({fetchPage: async before => {
    calls.push(before);
    if (before == null) return {events:[event(3),event(4)],has_more:true,next_before:3};
    return new Promise(resolve => {finish=resolve;});
  }});
  const viewport={scrollTop:0,scrollHeight:0,clientHeight:20,addEventListener(t,f){this[t]=f;}};
  const status={textContent:''};
  const sync=bindHistoryViewport(history, viewport, status, () => {viewport.scrollHeight=history.items.length*20;});
  history.onChange=sync;
  await history.loadOlder();
  assert.equal(viewport.scrollTop,20);
  viewport.scrollTop=0;
  const pending=viewport.scroll();
  assert.equal(status.textContent,'불러오는 중…');
  viewport.scroll();
  assert.equal(calls.length,2,'one request at a time');
  finish({events:[event(1),event(2),event(3)],has_more:false,next_before:null});
  await pending;
  assert.deepEqual(history.items.map(m=>m.seq),[1,2,3,4]);
  assert.equal(viewport.scrollTop,40,'prepended height preserves the visible message');
  assert.equal(status.textContent,'처음입니다');
  await viewport.scroll();
  assert.equal(calls.length,2);
  for(let i=5;i<=150;i++) history.ingest(event(i));
  assert.equal(history.items.length,150,'separate history is not the 120-item reducer feed');
  assert.equal(history.items[0].seq,1);
});

test('reload, live overlap, retry and stale responses keep cursor and messages correct', async () => {
  const {createMessenger}=await load();
  let resolve, fail=true;
  const history=createMessenger({fetchPage:async () => {
    if(fail) throw Error('offline');
    return new Promise(r=>resolve=r);
  }});
  await history.loadOlder();
  assert.match(history.label,/다시/);
  fail=false;
  const pending=history.loadOlder();
  history.ingest(event(2)); history.ingest(event(3));
  resolve({events:[event(1),event(2)],has_more:false,next_before:null});
  await pending;
  assert.deepEqual(history.items.map(m=>m.seq),[1,2,3]);
  history.ingest({...event(4),type:'request.followup',data:{id:'fu',agent_id:'analyst',text:'why'}});
  history.ingest({...event(5),type:'request.followup_done',data:{id:'fu',ok:true,answer:'because'}});
  assert.equal(history.items.at(-1).who,'analyst');
  history.reset();
  const stale=history.loadOlder(); history.reset();
  resolve({events:[event(99)],has_more:false,next_before:null}); await stale;
  assert.equal(history.items.length,0);
  const fresh=history.loadOlder();
  resolve({events:[event(1),event(2),event(3)],has_more:false,next_before:null}); await fresh;
  assert.deepEqual(history.items.map(m=>m.seq),[1,2,3]);
});

test('projection excludes tool/debug events and keeps questions, answers and reports', async () => {
  const {messageFromEvent}=await load();
  assert.equal(messageFromEvent({...event(1),type:'agent.tool'}),null);
  assert.equal(messageFromEvent({...event(1),data:{text:'trace',level:'debug'}}),null);
  assert.match(messageFromEvent({...event(1),type:'agent.ask',data:{question:'why',to:'cso'}}).text,/why/);
  assert.match(messageFromEvent({...event(2),type:'agent.answer',data:{answer:'because',question:'why'}}).text,/because.*why/);
  assert.match(messageFromEvent({...event(3),type:'request.completed',request_id:'r',data:{}}).text,/보고서/);
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.match(html,/bindHistoryViewport\(messenger/);
  assert.match(html,/api\/messenger/);
  assert.match(html,/messenger\.ingest\(ev\)/);
});
