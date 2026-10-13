// PR #532 second verification (wf532r2 1/6/11 web side, 16-18): no control row for consults and other non-step
// kinds; request end closes rows; a paused or halted step keeps the request at waiting_pi; a cancelled halt says so.
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
  const ev = (type, task_id, data = {}, agent_id = 'worker') => office.apply({ type, request_id: 'r', task_id, agent_id, data });
  office.apply({ type: 'snapshot', data: { agents: [{ id: 'worker', name: 'Worker' }], requests: [] } });
  office.apply({ type: 'request.created', request_id: 'r', data: { text: 'x', mode: 'orchestrate' } });
  office.apply({ type: 'request.plan', request_id: 'r', data: { steps: [{ id: 'A', agent_id: 'worker' }, { id: 'B', agent_id: 'worker' }] } });
  const worker = office.ag('worker');
  const rows = () => staffControls(worker, controlOf).map(v => [v.taskId, v.actions.join(',')]);

  // A consult, follow-up or review has no control row; a step does.
  for (const [tid, kind] of [['c1', 'consult'], ['f1', 'followup'], ['v1', 'review'], ['b1', 'briefing']]) {
    ev('task.dispatched', tid, { kind });
    ev('agent.status', tid, { state: 'working' });
  }
  assert.deepEqual(rows(), []);
  ev('task.dispatched', 's1', { kind: 'step', step_id: 'A' });
  ev('agent.status', 's1', { state: 'working' });
  assert.deepEqual(rows(), [['s1', 'steer,pause,halt']]);
  for (const tid of ['c1', 'f1', 'v1', 'b1']) ev('agent.status', tid, { state: 'done' });

  // 17: a paused step holds the request at waiting_pi; a quota hold on another step lifting returns there, not running.
  ev('agent.status', 's1', { state: 'done' });
  ev('task.result', 's1', { ok: true });
  ev('request.step_paused', 's1', { step_id: 'A' });
  office.apply({ type: 'request.status', request_id: 'r', data: { status: 'waiting_pi' } });
  office.apply({ type: 'request.step_quota_wait', request_id: 'r', data: { step_id: 'B', engine: 'codex', resume_at: 2000 } });
  office.apply({ type: 'request.step_quota_resumed', request_id: 'r', data: { step_id: 'B', engine: 'codex' } });
  assert.equal(office.S.requests.get('r').status, 'waiting_pi');

  // 18: a halt closed by request cancel says the request was cancelled, not that the deadline passed.
  ev('task.dispatched', 'h1', { kind: 'step', step_id: 'B' });
  ev('task.result', 'h1', { ok: false, error_kind: 'halted' });
  ev('request.step_halted', 'h1', { step_id: 'B' });
  ev('request.step_halt_expired', 'h1', { step_id: 'B', reason: 'cancelled' });
  const last = worker.log[worker.log.length - 1].text;
  assert.ok(last.includes('취소') && !last.includes('재개 기한'), last);

  // 16: an HPC-wait row and a paused row go when the request ends (cancelled), as after a reload.
  ev('task.dispatched', 'w1', { kind: 'step', step_id: 'B' });
  ev('agent.status', 'w1', { state: 'hibernating', jobs: ['42'] });
  ev('task.result', 'w1', { ok: true, pending_jobs: ['42'] });
  assert.deepEqual(rows(), [['s1', 'steer,resume'], ['w1', 'steer,pause']]);
  office.apply({ type: 'request.completed', request_id: 'r', data: { ok: false, status: 'cancelled' } });
  assert.deepEqual(rows(), []);
  console.log('staff control scope web tests passed');
})().catch(err => { console.error(err); process.exit(1); });
