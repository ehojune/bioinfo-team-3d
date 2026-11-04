// PR #532 review: a staff member running two tasks at once gets one control row per task, and a halt whose resume
// bound passed drops its resume row on open pages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const web = path.join(root, 'labhq/web');
const load = name => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(web, 'ui', name), 'utf8')).toString('base64'));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

(async () => {
  const { staffControls, controlHtml } = await load('control.js');
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(web, 'state.js'), 'utf8'), context);
  const office = context.LabHQState.createOfficeState({ now: () => 1000 });
  const controlOf = id => office.S.taskControl.get(id) || {};
  const status = (task_id, data) => office.apply({ type: 'agent.status', request_id: 'r', task_id, agent_id: 'worker', data });
  office.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [] } });
  office.apply({ type: 'request.created', request_id: 'r', data: { text: 'x', mode: 'orchestrate' } });
  office.apply({ type: 'request.plan', request_id: 'r', data: { steps: [{ id: 'a', agent_id: 'worker' }, { id: 'b', agent_id: 'worker' }] } });

  // Two tasks of the same staff member run at once (runner.max_parallel): one row each, each naming its task.
  status('t1', { state: 'working', task: '표 정리' });
  status('t2', { state: 'working', task: '그림 그리기' });
  const worker = office.ag('worker');
  let rows = staffControls(worker, controlOf);
  assert.deepEqual(rows.map(v => [v.taskId, v.title, v.actions.join(',')]),
    [['t1', '표 정리', 'steer,pause,halt'], ['t2', '그림 그리기', 'steer,pause,halt']]);
  const html = controlHtml(rows, esc);
  assert.equal((html.match(/class="staff-control"/g) || []).length, 2);
  assert.match(html, /data-task="t1"><p class="control-head">표 정리<\/p>/);
  assert.match(html, /data-task="t2"><p class="control-head">그림 그리기<\/p>/);

  // Pausing one task changes only its row.
  office.apply({ type: 'agent.control', request_id: 'r', task_id: 't2', agent_id: 'worker', data: { paused: true } });
  rows = staffControls(worker, controlOf);
  assert.deepEqual(rows.map(v => [v.taskId, v.status, v.actions.join(',')]),
    [['t1', '', 'steer,pause,halt'], ['t2', '일시 정지됨', 'steer,resume,halt']]);

  // The first task ends: the other still running keeps its row, so it can still be paused or halted.
  status('t1', { state: 'done' });
  rows = staffControls(worker, controlOf);
  assert.deepEqual(rows.map(v => v.taskId), ['t2']);
  office.apply({ type: 'agent.control', request_id: 'r', task_id: 't2', agent_id: 'worker', data: { paused: false } });
  status('t2', { state: 'error', error: 'x' });
  assert.deepEqual(staffControls(worker, controlOf), [], 'no running task, no controls');

  // A halt nobody resumed within the bound: the resume row goes and the step is shown as failed.
  office.apply({ type: 'task.dispatched', request_id: 'r', task_id: 't3', agent_id: 'worker', data: { step_id: 'a', kind: 'step' } });
  status('t3', { state: 'working' });
  office.apply({ type: 'task.result', request_id: 'r', task_id: 't3', agent_id: 'worker', data: { ok: false, error_kind: 'halted' } });
  status('t3', { state: 'idle', halted: true });
  office.apply({ type: 'request.step_halted', request_id: 'r', task_id: 't3', agent_id: 'worker', data: { step_id: 'a' } });
  assert.deepEqual(staffControls(worker, controlOf).map(v => v.actions), [['resume']]);
  office.apply({ type: 'request.step_halt_expired', request_id: 'r', task_id: 't3', agent_id: 'worker', data: { step_id: 'a' } });
  assert.deepEqual(staffControls(worker, controlOf), []);
  assert.equal(office.S.taskControl.has('t3'), false);
  assert.equal(office.S.requests.get('r').steps.a, 'error');

  // The page sends each row's own task id and direction text.
  const page = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  assert.match(page, /const row = btn\.closest\('\.staff-control'\), task = row\?\.dataset\.task, input = row\?\.querySelector\('textarea\.steer-text'\);/);
  console.log('staff control task rows web tests passed');
})().catch(err => { console.error(err); process.exit(1); });
