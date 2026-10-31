const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const web = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(web, 'state.js'), 'utf8'), context);
const create = () => context.LabHQState.createOfficeState();
const load = () => import('data:text/javascript;base64,' + fs.readFileSync(path.join(web, 'ui/tasks.js')).toString('base64'));
class Element {
  constructor() { this.children = []; this.textContent = ''; this.dataset = {}; }
  append(...rows) { this.children.push(...rows); }
  replaceChildren(...rows) { this.children = rows; }
  setAttribute() {}
  addEventListener() {}
}
global.document = {createElement: () => new Element()};
function setup() {
  const o = create();
  for (const rid of ['r1', 'r2']) {
    o.apply({type:'request.created', request_id:rid, data:{text:rid}});
    o.apply({type:'request.plan', request_id:rid, data:{steps:[{id:'s1', agent_id:'analyst'}]}});
    o.apply({type:'task.dispatched', request_id:rid, task_id:rid+'-task', agent_id:'analyst', data:{step_id:'s1', kind:'step'}});
  }
  const emit = (type, data, rid='r1') => o.apply({type, data, request_id:rid, task_id:rid+'-task', agent_id:'analyst'});
  return {o, emit, q:o.S.requests.get('r1')};
}

test('staff waiting labels distinguish local jobs, HPC, asks and terminal jobs', () => {
  const {o, emit} = setup();
  o.apply({type:'snapshot', data:{agents:[{id:'analyst'}], requests:[], staff_tasks:[]}});
  emit('request.created', {text:'local wait'});
  emit('request.plan', {steps:[{id:'s1', agent_id:'analyst'}]});
  emit('task.dispatched', {step_id:'s1', kind:'step'});
  const agent = o.ag('analyst');
  emit('job.submitted', {job_id:'local-one', name:'GSEA'});
  emit('task.result', {ok:true, pending_jobs:['local-one']});
  assert.equal(o.stateText(agent), '로컬 계산 기다리는 중');
  emit('job.state', {job_id:'local-one', state:'completed'});
  assert.equal(o.stateText(agent), '후속 처리 기다리는 중');
  emit('task.result', {ok:true, pending_jobs:['42']});
  assert.equal(o.stateText(agent), 'HPC 작업 기다리는 중');
  emit('task.result', {ok:true, pending_jobs:['local-two', '42']});
  assert.equal(o.stateText(agent), '로컬 계산·HPC 작업 기다리는 중');
  emit('task.result', {ok:true, pending_jobs:[], pending_asks:['ask-one']});
  assert.equal(o.stateText(agent), '답 기다리는 중');
});

test('task-less local wait survives roster refresh without changing HPC or ask labels', () => {
  const o = create();
  o.apply({type:'snapshot', data:{agents:[{id:'analyst'}]}});
  o.apply({type:'agent.status', agent_id:'analyst', data:{state:'hibernating', jobs:['local-one']}});
  o.apply({type:'roster.updated', data:{agents:[{id:'analyst', state:'hibernating'}]}});
  assert.equal(o.stateText(o.ag('analyst')), '로컬 계산 기다리는 중');
  o.apply({type:'job.state',agent_id:'analyst',data:{job_id:'local-one',state:'completed'}});
  assert.equal(o.stateText(o.ag('analyst')), '후속 처리 기다리는 중');
});

test('2.5D visible staff badge retains the local or followup wait label', async () => {
  const ui = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(web, 'ui/office-art.js')).toString('base64'));
  for (const detail of ['로컬 계산 기다리는 중', '로컬 계산·HPC 작업 기다리는 중', '후속 처리 기다리는 중', '작업·응답 대기'])
    assert.equal(ui.officeStatus({}, 'hibernating', detail).text, detail);
});

test('HPC rack excludes local calculations from its running count', () => {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const source = html.slice(html.indexOf('function renderRack()'), html.indexOf('function renderDoor()'));
  const rack = {dataset:{}, setAttribute(name,value) { this[name] = value; }};
  const S = {jobs:new Map([['local-one',{id:'local-one',state:'running'}], ['42',{id:'42',state:'running'}]])};
  vm.runInNewContext(source + '\nrenderRack();', {S, $:()=>rack, esc:String});
  assert.match(rack['aria-label'], /실행 중 1건/);
  const main = fs.readFileSync(path.join(web, 'lab3d/src/main.js'), 'utf8');
  const count = main.match(/const running=([^;]+);/)[1];
  assert.equal(vm.runInNewContext(count, {S}), 1, '3D rack uses the same HPC-only count');
});

test('task-less snapshot without job identity does not invent an HPC wait', async () => {
  const o = create();
  o.apply({type:'snapshot', data:{agents:[{id:'analyst'}], requests:[{id:'r1',status:'running',
    plan:{steps:[{id:'s1',agent_id:'analyst'}]},step_status:{s1:'hibernating'}}],staff_tasks:[]}});
  assert.equal(o.stateText(o.ag('analyst')), '작업·응답 대기');
  const flow = new Element();
  (await load()).syncStepFlow(flow, o.S.requests.get('r1'));
  assert.match(flow.children[0].textContent, /작업·응답 대기/);
  assert.doesNotMatch(flow.children[0].textContent, /HPC/);
});
test('local submission, state and request ownership reach the shared flow and task card', async () => {
  const {o, emit, q} = setup(), ui = await load();
  emit('job.submitted', {job_id:'local-one', name:'GSEA'});
  emit('job.submitted', {job_id:'local-other', name:'Other'}, 'r2');
  assert.match(o.S.feed.find(row => row.text.includes('GSEA')).text, /로컬 계산 제출/);
  emit('task.result', {ok:true, pending_jobs:['local-one']});
  const flow = new Element();
  ui.syncStepFlow(flow, q);
  assert.match(flow.children[0].textContent, /로컬 계산 GSEA: 실행 중.*진행률 모름/);
  assert.doesNotMatch(flow.children[0].textContent, /HPC|Other/);
  emit('job.state', {job_id:'local-one', state:'suspended'});
  ui.syncStepFlow(flow, q);
  assert.match(flow.children[0].textContent, /GSEA: 일시정지/);
  assert.equal(q.stepJobs.s1.length, 1);
  const board = new Element();
  ui.syncTaskBoard(board, q, o.S.stepDetails);
  const allText = node => node.textContent + node.children.map(allText).join(' ');
  assert.match(allText(board), /GSEA: 일시정지/);
  assert.doesNotMatch(allText(board), /Other/);
});
test('2.5D task card cache changes on job-only updates and step flow switches requests', async () => {
  const {o, emit, q} = setup(), ui = await load();
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const render = html.slice(html.indexOf('function renderRequest()'));
  const cache = render.slice(render.indexOf('  const key ='), render.indexOf('  if (key === reqKey)')) + '\nkey;';
  // The free variables of renderRequest's cache key: a new one there must be given here too (#877 deadlineText).
  const cacheContext = vm.createContext({q, S:o.S, staffNames:[], statusText:'', deadlineText:'', requestDigest:[], details:[], snapshotLoaded:true});
  emit('job.submitted', {job_id:'local-one', name:'GSEA'});
  const before = vm.runInContext(cache, vm.createContext({...cacheContext}));
  emit('job.state', {job_id:'local-one', state:'held'});
  const after = vm.runInContext(cache, vm.createContext({...cacheContext}));
  assert.notEqual(after, before, 'a job state change must invalidate the enclosing request card cache');
  const ticking = text => vm.runInContext(cache, vm.createContext({...cacheContext, deadlineText:text}));
  assert.notEqual(ticking('시간 상한 켜짐 90분 · 남은 80분'), ticking('시간 상한 켜짐 90분 · 남은 79분'),
    'the time-limit countdown is part of the key, so the 15 s timer redraws it');
  emit('job.submitted', {job_id:'local-other', name:'Other'}, 'r2');
  const flow = new Element();
  ui.syncStepFlow(flow, q);
  assert.match(flow.children[0].textContent, /GSEA/);
  ui.syncStepFlow(flow, o.S.requests.get('r2'));
  assert.match(flow.children[0].textContent, /Other/);
  assert.doesNotMatch(flow.children[0].textContent, /GSEA/);
  q.plan.push({id:'s2', agent_id:'analyst'}); q.steps.s2 = 'working';
  o.apply({type:'task.dispatched', request_id:'r1', task_id:'second-step', data:{step_id:'s2'}});
  o.apply({type:'job.submitted', request_id:'r1', task_id:'second-step', data:{job_id:'local-s2', name:'Second'}});
  ui.syncStepFlow(flow, q);
  assert.match(flow.children[0].textContent, /GSEA/);
  assert.doesNotMatch(flow.children[0].textContent, /Second/);
  assert.match(flow.children[1].textContent, /Second/);
});
for (const state of ['completed','failed','cancelled','unknown_finished','error'])
  test(`terminal job ${state} clears pending display without affecting another request`, async () => {
    const {o, emit, q} = setup();
    emit('job.submitted', {job_id:'local-one', name:'GSEA'});
    emit('job.submitted', {job_id:'local-other', name:'Other'}, 'r2');
    emit('task.result', {ok:true, pending_jobs:['local-one']});
    q.steps.s1 = 'hibernating';
    emit('job.state', {job_id:'local-one', state});
    assert.equal(q.stepJobs.s1.length, 0);
    assert.equal((await load()).stepWaitLabel('hibernating', q.stepJobs.s1), '후속 처리 대기');
    assert.equal(o.S.requests.get('r2').stepJobs.s1[0].name, 'Other');
  });
test('terminal request and step clear display even without a final job event', () => {
  for (const type of ['request.completed', 'request.step_done']) {
    const {emit, q} = setup();
    emit('job.submitted', {job_id:'local-one', name:'GSEA'});
    emit(type, type === 'request.completed' ? {ok:false, status:'cancelled'} : {step_id:'s1', ok:false});
    assert.equal(Object.keys(q.stepJobs).length, 0);
  }
});
for (const job_id of ['42', 'local-shared']) test(`same scheduler ID ${job_id} stays scoped to its task`, () => {
  const {o, emit, q} = setup();
  emit('job.submitted', {job_id, name:'First'});
  emit('job.submitted', {job_id, name:'Second'}, 'r2');
  emit('task.result', {ok:true, pending_jobs:[job_id]});
  emit('task.result', {ok:true, pending_jobs:[job_id]}, 'r2');
  emit('job.state', {job_id, state:'running'});
  assert.equal(q.stepJobs.s1[0].name, 'First');
  assert.equal(q.stepJobs.s1[0].state, 'running');
  assert.equal(o.S.requests.get('r2').stepJobs.s1[0].name, 'Second');
  assert.equal(o.S.requests.get('r2').stepJobs.s1[0].state, job_id.startsWith('local-') ? 'running' : 'queued');
  o.apply({type:'job.state', data:{job_id, state:'cancelled'}});
  assert.equal(q.stepJobs.s1[0].state, 'running', 'ambiguous events must not change owned jobs');
  emit('job.state', {job_id, state:'completed'});
  assert.equal(q.stepJobs.s1.length, 0);
  assert.equal(o.S.requests.get('r2').stepJobs.s1[0].name, 'Second');
});
test('next turn and authoritative empty snapshot remove old job ownership', () => {
  const {o, emit, q} = setup();
  emit('job.submitted', {job_id:'local-one', name:'Old turn'});
  o.apply({type:'task.dispatched', request_id:'r1', task_id:'next-turn', data:{step_id:'s1'}});
  assert.equal(Object.keys(q.stepJobs).length, 0);
  o.apply({type:'snapshot', data:{requests:[{id:'r1', status:'running', plan:{steps:[{id:'s1'}]}}],
    recent_events:[{type:'task.dispatched', request_id:'r1', task_id:'old', data:{step_id:'s1'}},
      {type:'job.submitted', request_id:'r1', task_id:'old', data:{job_id:'local-stale', name:'Old'}}],
    staff_tasks:[]}});
  assert.equal(Object.keys(o.S.requests.get('r1').stepJobs).length, 0);
});
test('snapshot pending jobs outside replay recover identity without inventing name or running state', async () => {
  const {o, emit} = setup();
  emit('job.submitted', {job_id:'local-stale', name:'Stale'});
  o.apply({type:'snapshot', data:{requests:[{id:'r1', status:'running', plan:{steps:[{id:'s1'}]}, step_status:{s1:'hibernating'}}],
    running_tasks:[{id:'old-task', request_id:'r1', step_id:'s1', state:'hibernating',
      activity:{state:'hibernating', jobs:['local-known-only-by-id']}}]}});
  const q = o.S.requests.get('r1'), flow = new Element();
  (await load()).syncStepFlow(flow, q);
  assert.match(flow.children[0].textContent, /계산 대기.*local-known-only-by-id: 상태 확인 중/);
  assert.doesNotMatch(flow.children[0].textContent, /Stale|실행 중/);
  o.apply({type:'snapshot', data:{requests:[{id:'r1', status:'running', plan:{steps:[{id:'s1'}]}}], staff_tasks:[]}});
  assert.equal(Object.keys(o.S.requests.get('r1').stepJobs).length, 0);
});
test('HPC keeps its identity, mixed jobs are explicit and 3D uses the shared label', async () => {
  const {o, emit, q} = setup(), ui = await load();
  emit('job.submitted', {job_id:'42', name:'Cluster'});
  assert.match(o.S.feed.find(row => row.text.includes('Cluster')).text, /HPC 작업 제출/);
  assert.equal(ui.stepWaitLabel('hibernating', q.stepJobs.s1), 'HPC 대기');
  emit('job.submitted', {job_id:'local-one', name:'<script> & GSEA'});
  assert.match(ui.stepWaitLabel('hibernating', q.stepJobs.s1), /로컬 계산·HPC 대기.*로컬 계산 <script> & GSEA/);
  const source = fs.readFileSync(path.join(web, 'lab3d/src/live.js'), 'utf8');
  assert.match(source, /import \{stepWaitLabel\} from '..\/..\/ui\/tasks.js'/);
  assert.match(source, /stepWaitLabel\(q.steps\[step.id\] \|\| 'pending', q.stepJobs\[step.id\]\)/);
  assert.match(source, /q.steps, q.stepJobs,/);
});
