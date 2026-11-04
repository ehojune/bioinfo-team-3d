// PR #532 verification round (wf532 [9]/[13], [14]): a steer or pause never clears a halt on open pages; rows of
// ended tasks go on their terminal events, as a reload would; a paused step keeps one row to resume it from.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const web = path.join(root, 'labhq/web');
const load = name => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(web, 'ui', name), 'utf8')).toString('base64'));

(async () => {
  const { staffControls } = await load('control.js');
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(web, 'state.js'), 'utf8'), context);
  const office = context.LabHQState.createOfficeState({ now: () => 1000 });
  const controlOf = id => office.S.taskControl.get(id) || {};
  const ev = (type, task_id, data = {}) => office.apply({ type, request_id: 'r', task_id, agent_id: 'worker', data });
  office.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [] } });
  office.apply({ type: 'request.created', request_id: 'r', data: { text: 'x', mode: 'orchestrate' } });
  office.apply({ type: 'request.plan', request_id: 'r', data: { steps: [{ id: 'A', agent_id: 'worker' }] } });
  const worker = office.ag('worker');
  const rows = () => staffControls(worker, controlOf).map(v => [v.taskId, v.actions.join(',')]);

  // [9]/[13] halted, then a steer or pause from the CLI: the resume row stays.
  ev('task.dispatched', 't3', { step_id: 'A', kind: 'step' });
  ev('agent.status', 't3', { state: 'working' });
  ev('task.result', 't3', { ok: false, error_kind: 'halted' });
  ev('agent.status', 't3', { state: 'idle', halted: true });
  ev('request.step_halted', 't3', { step_id: 'A' });
  ev('agent.control', 't3', { paused: false, halt: false, halted: false, action: 'steer', applied: true });
  assert.deepEqual(rows(), [['t3', 'resume']]);
  ev('agent.control', 't3', { paused: true, action: 'pause', source: 'gateway' });
  assert.deepEqual(rows(), [['t3', 'resume']], 'still halted: only resume');
  ev('request.step_resumed', 't3', { step_id: 'A' });
  assert.deepEqual(rows(), []);

  // [14] an HPC wait, then the wake turn: the old row goes when the new turn is dispatched, and the new one when it ends.
  ev('task.dispatched', 't1', { step_id: 'A', kind: 'step' });
  ev('agent.status', 't1', { state: 'hibernating', jobs: ['42'] });
  ev('task.result', 't1', { ok: true, pending_jobs: ['42'] });
  assert.deepEqual(rows(), [['t1', 'steer,pause']]);
  ev('task.dispatched', 't2', { step_id: 'A', kind: 'step' });
  ev('agent.status', 't2', { state: 'working' });
  assert.deepEqual(rows(), [['t2', 'steer,pause,halt']]);
  ev('agent.status', 't2', { state: 'done' });
  ev('task.result', 't2', { ok: true });
  assert.deepEqual(rows(), [], 'nothing left once the step is over');

  // A paused step whose next turn waits: one row from its last task, until the turn is let go.
  ev('task.dispatched', 't5', { step_id: 'A', kind: 'step' });
  ev('agent.status', 't5', { state: 'working' });
  ev('agent.control', 't5', { paused: true, action: 'pause' });
  ev('agent.status', 't5', { state: 'done' });
  ev('task.result', 't5', { ok: true });
  assert.deepEqual(rows(), []);
  ev('request.step_paused', 't5', { step_id: 'A' });
  assert.deepEqual(rows(), [['t5', 'steer,resume']]);
  assert.equal(office.S.requests.get('r').steps.A, 'paused');
  ev('request.step_unpaused', 't5', { step_id: 'A' });
  assert.deepEqual(rows(), []);

  // A reload of that paused step restores the same row.
  const restored = context.LabHQState.createOfficeState({ now: () => 1000 });
  restored.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [
    { id: 'r', status: 'waiting_pi', mode: 'orchestrate', plan: { steps: [{ id: 'A', agent_id: 'worker' }] } }],
    running_tasks: [{ id: 't5', request_id: 'r', state: 'paused', step_id: 'A', agent_id: 'worker', control: { paused: true } }] } });
  assert.deepEqual(staffControls(restored.ag('worker'), id => restored.S.taskControl.get(id) || {}).map(v => [v.taskId, v.actions.join(',')]),
    [['t5', 'steer,resume']]);

  // Steers the step never delivered: the PI is told in the staff log and the feed.
  ev('agent.control', 't5', { action: 'undelivered', undelivered: ['마지막 지시'], source: 'gateway' });
  assert.ok(worker.log.some(line => line.text.includes('전달 못 한 방향 수정 1개')));
  assert.ok(office.S.feed.some(item => item.cls === 'alert' && item.text.includes('마지막 지시')));
  console.log('staff control step web tests passed');
})().catch(err => { console.error(err); process.exit(1); });
