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
  assert.equal(office.S.taskStep.get('t9'), 's2', 'the task is still recorded');
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

test('the 2.5D page closes its previous socket before opening a new one, as the 3D page does', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const open = /const open = \(\) => \{[\s\S]*?new WebSocket/.exec(page);
  assert.ok(open, 'open() found in index.html');
  assert.match(open[0], /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
  const live = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');
  assert.match(live, /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
});
