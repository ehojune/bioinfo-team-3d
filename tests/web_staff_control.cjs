// #59 ①: the staff sheet shows steer / pause-resume / halt only while the staff member runs a task.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const web = path.join(root, 'labhq/web');
const load = name => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(web, 'ui', name), 'utf8')).toString('base64'));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

(async () => {
  const { staffControl, staffControls, controlHtml, controlKey } = await load('control.js');
  const working = { state: 'working', taskId: 't1' };
  assert.deepEqual(staffControl({ state: 'working' }).actions, [], 'no task, no buttons');
  assert.deepEqual(staffControl(working).actions, ['steer', 'pause', 'halt']);
  assert.equal(staffControl(working).status, '');
  for (const state of ['queued', 'waiting']) assert.deepEqual(staffControl({ state, taskId: 't1' }).actions, ['steer', 'pause', 'halt']);
  for (const state of ['idle', 'done', 'error']) assert.deepEqual(staffControl({ state, taskId: 't1' }).actions, [], state);
  // Waiting for HPC: the wake turn takes a steer or a pause; there is no running CLI to halt.
  assert.deepEqual(staffControl({ state: 'hibernating', taskId: 't1' }).actions, ['steer', 'pause']);
  const paused = staffControl(working, { paused: true });
  assert.deepEqual(paused.actions, ['steer', 'resume', 'halt']);
  assert.equal(paused.status, '일시 정지됨');
  // A turn held before it starts is queued and paused; one that ended paused keeps resume for the session's next turn.
  assert.deepEqual(staffControl({ state: 'queued', taskId: 't2' }, { paused: true }).actions, ['steer', 'resume', 'halt']);
  assert.deepEqual(staffControl({ state: 'idle', taskId: 't2' }, { paused: true }).actions, ['steer', 'resume']);
  // #59 ②: a task the runaway breaker limited shows a release button, and only while it runs.
  const limited = staffControl(working, { constrained: true });
  assert.deepEqual(limited.actions, ['steer', 'pause', 'halt', 'release_breaker']);
  assert.equal(limited.status, '도구 제한 중');
  assert.deepEqual(staffControl({ state: 'idle', taskId: 't1' }, { constrained: true }).actions, []);
  const halted = staffControl({ state: 'idle', taskId: 't1' }, { halted: true, halt: true });
  assert.deepEqual(halted.actions, ['resume']);
  assert.equal(halted.status, '멈춤 · 재개 가능');
  assert.notEqual(controlKey([paused]), controlKey([staffControl(working)]));

  const html = controlHtml([staffControl({ state: 'working', taskId: 't"1', title: '표 정리' })], esc);
  assert.match(html, /data-task="t&quot;1"/);
  assert.match(html, /<p class="control-head">표 정리<\/p>/);
  assert.match(html, /<textarea class="steer-text"[^>]*maxlength="2000"/);
  assert.match(html, /data-act="steer">방향 보내기</);
  assert.match(html, /data-act="pause">일시 정지</);
  assert.match(html, /class="btn warn" data-act="halt">멈추기</);
  assert.equal(controlHtml([], esc), '');
  assert.doesNotMatch(controlHtml([halted], esc), /textarea/);

  // The reducer keeps each task's control state and the staff member's live tasks, out of the legacy digests.
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(web, 'state.js'), 'utf8'), context);
  const office = context.LabHQState.createOfficeState({ now: () => 1000 });
  const controlOf = id => office.S.taskControl.get(id) || {};
  office.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [] } });
  office.apply({ type: 'request.created', request_id: 'r', data: { text: 'x', mode: 'orchestrate' } });
  office.apply({ type: 'request.plan', request_id: 'r', data: { steps: [{ id: 's1', agent_id: 'worker' }] } });
  office.apply({ type: 'task.dispatched', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { step_id: 's1', kind: 'step' } });
  office.apply({ type: 'agent.status', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { state: 'working' } });
  const worker = office.ag('worker');
  assert.deepEqual([...worker.tasks.keys()], ['t1']);
  assert.ok(!Object.keys(worker).includes('tasks'), 'not enumerable: the golden state hashes stay');
  office.apply({ type: 'agent.control', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { paused: true, mode: 'hook' } });
  assert.equal(office.S.taskControl.get('t1').paused, true);
  assert.deepEqual(staffControls(worker, controlOf).map(v => v.actions), [['steer', 'resume', 'halt']]);
  office.apply({ type: 'task.result', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { ok: false, error_kind: 'halted' } });
  office.apply({ type: 'agent.status', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { state: 'idle', halted: true } });
  assert.equal(office.S.requests.get('r').steps.s1, 'halted', 'a halted step is not shown as failed');
  assert.deepEqual(staffControls(worker, controlOf).map(v => v.actions), [['resume']]);
  office.apply({ type: 'request.step_resumed', request_id: 'r', task_id: 't1', agent_id: 'worker', data: { step_id: 's1' } });
  assert.equal(office.S.taskControl.has('t1'), false);
  assert.deepEqual(staffControls(worker, controlOf), []);
  assert.equal(office.S.requests.get('r').steps.s1, 'working');

  // A reload restores both from the snapshot's running tasks.
  const restored = context.LabHQState.createOfficeState({ now: () => 1000 });
  restored.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [
    { id: 'r', status: 'waiting_pi', mode: 'orchestrate', plan: { steps: [{ id: 's1', agent_id: 'worker' }] } }],
    running_tasks: [{ id: 't9', request_id: 'r', state: 'halted', step_id: 's1', agent_id: 'worker', control: { halt: true } }] } });
  assert.deepEqual([...restored.ag('worker').tasks.keys()], ['t9']);
  assert.deepEqual({ ...restored.S.taskControl.get('t9') }, { halt: true, halted: true });
  assert.equal(restored.S.requests.get('r').steps.s1, 'halted');

  // The page wires the module and the REST call; demo mode (no api) shows no controls.
  const page = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  assert.match(page, /import \{controlHtml, controlKey, staffControls\} from '\.\/ui\/control\.js';/);
  assert.match(page, /control: \(id, action, text\) => post\(`\/api\/tasks\/\$\{encodeURIComponent\(id\)\}\/\$\{action === 'release_breaker' \? 'breaker\/release' : action\}`/);
  assert.match(page, /return api\?\.control \? staffControls\(a, tid => S\.taskControl\.get\(tid\) \|\| \{\}\) : \[\];/);
  const board = await load('tasks.js');
  assert.ok(board.syncTaskBoard);
  console.log('staff control web tests passed');
})().catch(err => { console.error(err); process.exit(1); });
