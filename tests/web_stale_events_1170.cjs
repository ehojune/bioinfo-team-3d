// #1170: a finished request and a settled card stay settled when a duplicate or late event arrives, and a
// reconnect never leaves two sockets open. Reducer calls plus a source check of the 2.5D page's socket handling.
// The pages keep their own lastSeq (tests/test_web.py and web_cards_status.cjs pin that text); the reducer's guard
// makes them safe for any caller that has none.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const root = path.resolve(__dirname, '..', 'labhq/web');
const context = vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(root, 'state.js'), 'utf8'), context);
const L = context.LabHQState;

const snapshot = (seq, extra = {}) => ({type: 'snapshot', seq, data: {agents: [{id: 'analyst', name: 'analyst', engine: 'mock'}],
  requests: [], approvals: [], recent_events: [], ...extra}});
const fresh = () => { const office = L.createOfficeState({now: () => 100}); office.apply(snapshot(1)); return office; };
const created = (office, seq = 2, rid = 'r1') => office.apply({type: 'request.created', seq, request_id: rid,
  data: {text: 'x', mode: 'orchestrate', status: 'running'}});
const completed = (office, seq = 9, rid = 'r1') => office.apply({type: 'request.completed', seq, request_id: rid,
  data: {status: 'done', ok: true}});
const dispatched = (office, seq, tid, kind = 'step', rid = 'r1') => office.apply({type: 'task.dispatched', seq,
  agent_id: 'analyst', task_id: tid, request_id: rid, data: {kind, step_id: kind === 'step' ? 's1' : undefined, title: 't'}});

test('the same seq is applied once by the reducer itself', () => {
  const office = fresh();
  created(office, 2);
  completed(office, 9);
  created(office, 2);
  const q = office.S.requests.get('r1');
  assert.equal(q.status, 'done'); assert.equal(q.phase, 'done');
  office.apply({type: 'approval.requested', seq: 10, request_id: 'r1', data: {id: 'a1', kind: 'research_plan', summary: 's'}});
  office.apply({type: 'approval.resolved', seq: 11, request_id: 'r1', data: {id: 'a1', approved: true}});
  office.apply({type: 'approval.requested', seq: 10, request_id: 'r1', data: {id: 'a1', kind: 'research_plan', summary: 's'}});
  assert.equal(office.S.approvals.size, 0, 'a settled card is not shown again by a repeated event');
  assert.equal(office.S.lastSeq, 11);
});

test('a snapshot restarts the sequence and its replayed events are not dropped', () => {
  const office = fresh();
  created(office, 2); completed(office, 9);
  office.apply(snapshot(20, {requests: [{id: 'r1', text: 'x', status: 'done', mode: 'orchestrate'}],
    recent_events: [{type: 'request.created', seq: 2, request_id: 'r1', data: {text: 'x', mode: 'orchestrate', status: 'running'}},
      {type: 'approval.requested', seq: 3, request_id: 'r1', data: {id: 'a1', kind: 'research_plan', summary: 's'}},
      {type: 'request.completed', seq: 9, request_id: 'r1', data: {status: 'done'}}]}));
  const q = office.S.requests.get('r1');
  assert.equal(office.S.lastSeq, 20);
  assert.equal(q.status, 'done'); assert.equal(q.phase, 'done');
  assert.equal(office.S.approvals.size, 0, 'the snapshot decides which cards are pending');
  created(office, 21, 'r2');
  assert.ok(office.S.requests.has('r2'), 'an event after the snapshot is applied');
  office.apply({type: 'request.created', seq: 15, request_id: 'r3', data: {text: 'z', mode: 'orchestrate', status: 'running'}});
  assert.equal(office.S.requests.has('r3'), false, 'an event older than the snapshot is ignored');
});

test('events without a seq (demo, local notices) still apply', () => {
  const office = fresh();
  office.apply({type: 'request.created', request_id: 'r1', data: {text: 'x', mode: 'orchestrate', status: 'running'}});
  office.apply({type: 'request.created', request_id: 'r2', data: {text: 'y', mode: 'orchestrate', status: 'running'}});
  assert.ok(office.S.requests.has('r1') && office.S.requests.has('r2'));
});

test('a late step dispatch does not reopen a finished request', () => {
  const office = fresh();
  created(office, 2); dispatched(office, 3, 't1'); completed(office, 9);
  office.apply({type: 'task.dispatched', seq: 10, agent_id: 'analyst', task_id: 't9', request_id: 'r1',
    data: {kind: 'step', step_id: 's2', title: 'late'}});
  const q = office.S.requests.get('r1');
  assert.equal(q.status, 'done'); assert.equal(q.phase, 'done', 'the phase stays done');
  assert.equal(q.steps.s2, undefined, 'the late step is not shown as working');
  assert.equal(office.S.taskStep.has('t9'), false, 'the refused dispatch leaves no step ownership');
});

test("a late status of a finished request's pipeline task adds no staff row; a follow-up turn still does", () => {
  const office = fresh();
  created(office, 2); dispatched(office, 3, 't1'); completed(office, 9);
  office.apply({type: 'agent.status', seq: 10, agent_id: 'analyst', task_id: 't1', request_id: 'r1', data: {state: 'working', task: 'late'}});
  assert.equal(office.ag('analyst').tasks.has('t1'), false);
  dispatched(office, 11, 'f1', 'followup');
  office.apply({type: 'agent.status', seq: 12, agent_id: 'analyst', task_id: 'f1', request_id: 'r1', data: {state: 'working', task: 'follow-up'}});
  assert.equal(office.ag('analyst').tasks.has('f1'), true, 'a follow-up turn runs on a finished request');
});

test('a late review does not move a finished request out of done', () => {
  const office = fresh();
  created(office, 2); completed(office, 9);
  office.apply({type: 'request.review', seq: 10, request_id: 'r1', data: {verdict: 'revise', issues: []}});
  assert.equal(office.S.requests.get('r1').phase, 'done');
});

test('a running request still follows its steps, review and completion', () => {
  const office = fresh();
  created(office, 2); dispatched(office, 3, 't1');
  assert.equal(office.S.requests.get('r1').phase, 'execute');
  office.apply({type: 'request.review', seq: 4, request_id: 'r1', data: {verdict: 'accept', issues: []}});
  assert.equal(office.S.requests.get('r1').phase, 'review');
  completed(office, 5);
  assert.equal(office.S.requests.get('r1').phase, 'done');
});

const settled = status => { const office = fresh(); created(office, 2); dispatched(office, 3, 't1');
  office.apply({type: 'request.completed', seq: 9, request_id: 'r1', data: {status, ok: status === 'done'}}); return office; };

for (const status of ['done', 'failed', 'cancelled', 'rejected', 'review_pending']) {
  test(`${status}: an event that starts or holds pipeline work leaves the request, its staff and its ledger alone`, () => {
    const office = settled(status);
    const before = JSON.stringify({q: office.S.requests.get('r1'), tasks: [...office.S.activeTasks.keys()],
      agent: office.ag('analyst').state, task: office.ag('analyst').task, rows: [...office.ag('analyst').tasks.keys()]});
    dispatched(office, 10, 't9');
    office.apply({type: 'agent.status', seq: 11, agent_id: 'analyst', task_id: 't1', request_id: 'r1', data: {state: 'working', task: 'late'}});
    office.apply({type: 'agent.status', seq: 12, agent_id: 'analyst', task_id: 't9', request_id: 'r1', data: {state: 'queued', task: 'late'}});
    office.apply({type: 'request.review', seq: 13, request_id: 'r1', data: {verdict: 'revise', issues: [{step_id: 's1'}]}});
    office.apply({type: 'request.step_attempt', seq: 14, request_id: 'r1', data: {step_id: 's1', attempt: 2}});
    office.apply({type: 'request.step_quota_wait', seq: 15, request_id: 'r1', data: {step_id: 's1'}});
    office.apply({type: 'request.plan', seq: 16, request_id: 'r1', data: {steps: []}});
    office.apply({type: 'request.resumed', seq: 17, request_id: 'r1', data: {}});
    const after = JSON.stringify({q: office.S.requests.get('r1'), tasks: [...office.S.activeTasks.keys()],
      agent: office.ag('analyst').state, task: office.ag('analyst').task, rows: [...office.ag('analyst').tasks.keys()]});
    assert.equal(after, before);
    assert.equal(office.S.lastSeq, 17, 'the dropped events still count as seen');
  });
}

test('what settles or follows a finished request still applies: done/idle status, step end, follow-up, usage, cards', () => {
  const office = settled('done');
  office.apply({type: 'agent.status', seq: 10, agent_id: 'analyst', task_id: 't1', request_id: 'r1', data: {state: 'done'}});
  assert.equal(office.ag('analyst').state, 'done');
  office.apply({type: 'request.step_done', seq: 11, request_id: 'r1', data: {step_id: 's1', ok: true}});
  assert.equal(office.S.requests.get('r1').steps.s1, 'done');
  dispatched(office, 12, 'f1', 'followup');
  office.apply({type: 'agent.status', seq: 13, agent_id: 'analyst', task_id: 'f1', request_id: 'r1', data: {state: 'working', task: 'follow-up'}});
  assert.equal(office.ag('analyst').state, 'working');
  assert.ok(office.S.activeTasks.has('f1'), 'a follow-up turn is on the ledger');
  office.apply({type: 'approval.requested', seq: 14, request_id: 'r1', data: {id: 'a9', kind: 'result_storage', summary: 's'}});
  assert.ok(office.S.approvals.has('a9'), 'a card of a finished request is shown');
});

test('a snapshot replay applies the same rule: a pipeline dispatch or status after the completion leaves no working staff', () => {
  const office = L.createOfficeState({now: () => 100});
  const ev = (seq, type, extra = {}) => ({type, seq, request_id: 'r1', ...extra});
  office.apply(snapshot(30, {requests: [{id: 'r1', text: 'x', status: 'done', mode: 'orchestrate'}],
    recent_events: [ev(2, 'request.created', {data: {text: 'x', mode: 'orchestrate', status: 'running'}}),
      ev(3, 'task.dispatched', {agent_id: 'analyst', task_id: 't1', data: {kind: 'step', step_id: 's1', title: 't'}}),
      ev(9, 'request.completed', {data: {status: 'done', ok: true}}),
      ev(10, 'task.dispatched', {agent_id: 'analyst', task_id: 't9', data: {kind: 'step', step_id: 's2', title: 'late'}}),
      ev(11, 'agent.status', {agent_id: 'analyst', task_id: 't9', data: {state: 'working', task: 'late'}})]}));
  const analyst = office.ag('analyst');
  assert.equal(analyst.tasks.size, 0, 'no staff row for the late task');
  assert.notEqual(analyst.state, 'working');
  assert.equal(office.S.activeTasks.size, 0);
  assert.equal(office.S.requests.get('r1').phase, 'done');
});

test('a status of a task whose kind is unknown does not start work for a finished request', () => {
  const office = settled('done');
  office.apply({type: 'agent.status', seq: 10, agent_id: 'analyst', task_id: 'tz', request_id: 'r1', data: {state: 'working', task: 'who knows'}});
  assert.equal(office.ag('analyst').tasks.has('tz'), false);
  assert.notEqual(office.ag('analyst').state, 'working');
  office.apply({type: 'agent.status', seq: 11, agent_id: 'analyst', task_id: 'tz', request_id: 'r1', data: {state: 'done'}});
  assert.equal(office.ag('analyst').state, 'done', 'an ending status still applies');
});

test('the 2.5D page closes its previous socket before opening a new one, as the 3D page does', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const open = /const open = \(\) => \{[\s\S]*?new WebSocket/.exec(page);
  assert.ok(open, 'open() found in index.html');
  assert.match(open[0], /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
  const live = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');
  assert.match(live, /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
});
