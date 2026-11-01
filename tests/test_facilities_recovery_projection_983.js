const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../labhq/web/state.js'), 'utf8'), context);
const office = context.LabHQState.createOfficeState();
const event = (seq, status) => ({type: 'request.facilities_projection', request_id: 'r', seq,
  data: {s1: {fix_status: status, rerun_status: 'unknown', step_status: 'unknown'}}});
office.apply({type: 'snapshot', seq: 1, data: {requests: [{id: 'r', status: 'running',
  facilities_projection: {s1: {fix_status: 'unknown'}}}]}});
office.apply(event(3, 'applied'));
office.apply(event(2, 'failed'));
office.apply(event(3, 'failed'));
assert.equal(office.S.stepDetails.get('r:s1').facilities_projection.fix_status, 'applied');
office.apply({type: 'snapshot', seq: 2, data: {requests: [{id: 'r', status: 'running',
  facilities_projection: {s1: {fix_status: 'failed'}}}]}});
assert.equal(office.S.stepDetails.get('r:s1').facilities_projection.fix_status, 'applied');
office.apply({type: 'snapshot', seq: 4, data: {requests: [{id: 'r', status: 'running',
  facilities_projection: {s1: {fix_status: 'succeeded', rerun_status: 'failed'}}}]}});
assert.equal(office.S.stepDetails.get('r:s1').facilities_projection.rerun_status, 'failed');
office.apply({type:'snapshot', seq:5, data:{recent_events:[event(3, 'applied')],
  requests:[{id:'r', status:'running', facilities_projection:{}}]}});
assert.equal(office.S.stepDetails.get('r:s1').facilities_projection, undefined);
console.log('facilities projection ordering passed');

(async () => {
  const source = fs.readFileSync(path.join(__dirname, '../labhq/web/ui/tasks.js'), 'utf8');
  const {facilitiesLines} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  const lines = facilitiesLines({requested_to:'facilities', routed_to:'cso', answered_by:'cso',
    answer_status:'rejected', answer:'offline', approval_status:'timed_out', fix_status:'applied',
    rerun_status:'unknown', step_status:'failed'}).join('\n');
  assert.match(lines, /담당: cso/);
  assert.match(lines, /시간 초과/);
  assert.match(lines, /수정: 적용 · 재실행: 확인 안 됨 · 원 단계: 실패/);
  const index = fs.readFileSync(path.join(__dirname, '../labhq/web/index.html'), 'utf8');
  const live = fs.readFileSync(path.join(__dirname, '../labhq/web/lab3d/src/live.js'), 'utf8');
  assert.match(index, /S\.stepDetails\.get\(`\$\{q\.id\}:direct`\)/);
  assert.match(live, /facilitiesLines\(direct\)/);
  const elements = [];
  function element(tag) { return {tag, children: [], dataset: {}, append(c) {this.children.push(c)},
    replaceChildren(...c) {this.children = c}, addEventListener() {}, setAttribute() {}}; }
  global.document = {createElement(tag) { const e = element(tag); elements.push(e); return e; }};
  const board = element('div');
  const details = new Map([['r:direct', {facilities_projection: {fix_status:'applied'}}]]);
  const {syncTaskBoard} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  syncTaskBoard(board, {id:'r', plan:[], steps:{}}, details);
  assert(elements.some(e => String(e.textContent).includes('수정: 적용')));
  details.set('r:s1', {facilities_projection:{fix_status:'failed'}});
  syncTaskBoard(board, {id:'r', plan:[{id:'s1'}], steps:{}}, details);
  const content = el => (el.textContent || '') + el.children.map(content).join('');
  assert.match(content(board), /수정: 실패/);
  details.get('r:s1').facilities_projection.fix_status = 'succeeded';
  syncTaskBoard(board, {id:'r', plan:[{id:'s1'}], steps:{}}, details);
  assert.match(content(board), /수정: 성공/);
  assert.doesNotMatch(content(board), /수정: 실패/);
  // Exercise the real 3D renderer with the isolated DOM/socket harness.
  const harness = fs.readFileSync(path.join(__dirname, 'web_live.cjs'), 'utf8').split('(async()=>{')[0]
    .replace("const root = path.resolve(__dirname, '..', 'labhq/web');", `const root = ${JSON.stringify(path.resolve(__dirname, '../labhq/web'))};`);
  const scenario = `(async()=>{
    const {startLiveOffice} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
    startLiveOffice(()=>{}); const ws=sockets[0]; ws.readyState=1; ws.onopen();
    const send = ev => ws.onmessage({data:JSON.stringify(ev)});
    const row = id => element('requests').children.find(x=>x.dataset.requestId===id);
    send({type:'snapshot',seq:1,data:{requests:[{id:'r',status:'running',plan:{steps:[{id:'s1'}]}}]}});
    send({type:'request.facilities_projection',seq:2,request_id:'r',data:{s1:{fix_status:'applied'},direct:{fix_status:'failed'}}});
    assert.match(textIn(row('r')),/수정: 적용/); assert.match(textIn(row('r')),/수정: 실패/);
    send({type:'request.facilities_projection',seq:3,request_id:'r',data:{s1:{fix_status:'succeeded'}}});
    assert.match(textIn(row('r')),/수정: 성공/); assert.doesNotMatch(textIn(row('r')),/수정: 실패/);
    send({type:'snapshot',seq:4,data:{requests:[{id:'r',status:'running',plan:{steps:[{id:'s1'}]},facilities_projection:{s1:{fix_status:'succeeded'}}}]}});
    assert.match(textIn(row('r')),/수정: 성공/);
  })().catch(e=>{console.error(e);process.exitCode=1});`;
  const result = require('node:child_process').spawnSync(process.execPath, ['-e', harness + scenario], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stdout + result.stderr);
  console.log('shared 2D/3D labels passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
