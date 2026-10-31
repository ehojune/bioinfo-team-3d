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

test('only a follow-up turn may start after the end: any other kind is refused, listed or not', () => {
  for (const kind of ['facilities_fix', 'blind_draft', 'wrap_up', 'hpc_wake', 'something_new']) {
    const office = settled('failed');
    office.apply({type: 'task.dispatched', seq: 10, agent_id: 'analyst', task_id: `x-${kind}`, request_id: 'r1', data: {kind, title: 't'}});
    office.apply({type: 'agent.status', seq: 11, agent_id: 'analyst', task_id: `x-${kind}`, request_id: 'r1', data: {state: 'working', task: 't'}});
    assert.equal(office.ag('analyst').tasks.has(`x-${kind}`), false, kind);
    assert.equal(office.S.activeTasks.has(`x-${kind}`), false, kind);
    assert.notEqual(office.ag('analyst').state, 'working', kind);
  }
});

test('a snapshot does not restore a pipeline task of a finished request, but keeps its running follow-up turn', () => {
  const office = L.createOfficeState({now: () => 100});
  const staffTask = (id, kind, extra = {}) => ({id, request_id: 'r1', agent_id: 'analyst', kind, state: 'running', title: 't', ...extra});
  office.apply(snapshot(40, {requests: [{id: 'r1', text: 'x', status: 'done', mode: 'orchestrate'}],
    running_tasks: [staffTask('t9', 'step', {step_id: 's2'}), staffTask('f1', 'followup')]}));
  assert.equal(office.S.activeTasks.has('t9'), false, 'the stale pipeline task is not on the ledger');
  assert.equal(office.ag('analyst').tasks.has('t9'), false);
  assert.equal(office.S.requests.get('r1').steps.s2, undefined);
  assert.equal(office.S.activeTasks.has('f1'), true, 'the follow-up turn is');
  assert.equal(office.S.requests.get('r1').phase, 'done');
});

test('a snapshot keeps a follow-up turn in the shape the gateway sends it: step_id "followup", no kind', () => {
  const office = L.createOfficeState({now: () => 100});
  office.apply(snapshot(50, {requests: [{id: 'r1', text: 'x', status: 'failed', mode: 'orchestrate'}],
    staff_tasks: [{id: 'f1', request_id: 'r1', agent_id: 'analyst', step_id: 'followup', title: 'ask', state: 'running',
      dispatched_at: 1, activity: {}},
      {id: 't9', request_id: 'r1', agent_id: 'analyst', step_id: 'step_2', kind: 'step', title: 'late', state: 'queued',
        dispatched_at: 2, activity: {}}]}));
  assert.equal(office.S.activeTasks.has('f1'), true, 'the follow-up turn is on the ledger');
  assert.equal(office.S.activeTasks.has('t9'), false, 'the stale pipeline task is not');
  office.apply({type: 'agent.status', seq: 51, agent_id: 'analyst', task_id: 'f1', request_id: 'r1', data: {state: 'working', task: 'ask'}});
  assert.equal(office.ag('analyst').tasks.has('f1'), true, 'its later status is recognised as a follow-up turn');
});

test("a snapshot's request status is known before its events replay: a completion older than the window still counts", () => {
  const office = L.createOfficeState({now: () => 100});
  office.apply(snapshot(60, {requests: [{id: 'r1', text: 'x', status: 'done', mode: 'orchestrate'}],
    recent_events: [  // request.created and request.completed are older than the 200-event window
      {type: 'task.dispatched', seq: 58, request_id: 'r1', agent_id: 'analyst', task_id: 't9', data: {kind: 'step', step_id: 's2', title: 'late'}},
      {type: 'agent.status', seq: 59, request_id: 'r1', agent_id: 'analyst', task_id: 't9', data: {state: 'working', task: 'late'}}]}));
  assert.notEqual(office.ag('analyst').state, 'working');
  assert.equal(office.ag('analyst').tasks.size, 0);
  assert.equal(office.S.activeTasks.size, 0);
  assert.equal(office.S.requests.get('r1').status, 'done');
});

for (const status of ['done', 'failed', 'cancelled', 'rejected', 'review_pending']) {
  test(`${status}: a late step resume or quota/login release does not turn a finished step back into work`, () => {
    const office = settled(status);
    office.apply({type: 'request.step_done', seq: 10, request_id: 'r1', data: {step_id: 's1', ok: true}});
    const before = JSON.stringify(office.S.requests.get('r1'));
    for (const [seq, type] of [[11, 'request.step_resumed'], [12, 'request.step_quota_resumed'], [13, 'request.step_login_resumed']])
      office.apply({type, seq, agent_id: 'analyst', task_id: 't1', request_id: 'r1', data: {step_id: 's1'}});
    assert.equal(JSON.stringify(office.S.requests.get('r1')), before);
    assert.equal(office.S.requests.get('r1').steps.s1, 'done');
  });
}

test('on a running request a step resume and a quota/login release still apply', () => {
  const office = fresh();
  created(office, 2); dispatched(office, 3, 't1');
  office.apply({type: 'request.step_quota_wait', seq: 4, request_id: 'r1', data: {step_id: 's1'}});
  assert.equal(office.S.requests.get('r1').steps.s1, 'waiting_quota');
  office.apply({type: 'request.step_quota_resumed', seq: 5, request_id: 'r1', data: {step_id: 's1'}});
  assert.equal(office.S.requests.get('r1').steps.s1, 'pending');
  office.apply({type: 'request.step_login_wait', seq: 6, request_id: 'r1', data: {step_id: 's1'}});
  office.apply({type: 'request.step_login_resumed', seq: 7, request_id: 'r1', data: {step_id: 's1'}});
  assert.notEqual(office.S.requests.get('r1').steps.s1, 'waiting_login');
});

for (const status of ['done', 'failed', 'cancelled', 'rejected', 'review_pending']) {
  test(`${status}: a late review that asks for a revision, with issues, leaves the finished steps alone`, () => {
    const office = settled(status);
    office.apply({type: 'request.step_done', seq: 10, request_id: 'r1', data: {step_id: 's1', ok: true}});
    office.apply({type: 'request.review', seq: 11, request_id: 'r1', data: {verdict: 'revise',
      issues: [{step_id: 's1', text: 'redo the table'}]}});
    const q = office.S.requests.get('r1');
    assert.equal(q.steps.s1, 'done', 'not revise');
    assert.equal(q.review, undefined, 'the late review is not recorded');
    assert.equal(q.phase, 'done');
  });
}

test('on a running request a review that asks for a revision still marks the step', () => {
  const office = fresh();
  created(office, 2); dispatched(office, 3, 't1');
  office.apply({type: 'request.step_done', seq: 4, request_id: 'r1', data: {step_id: 's1', ok: true}});
  office.apply({type: 'request.review', seq: 5, request_id: 'r1', data: {verdict: 'revise', issues: [{step_id: 's1', text: 'redo'}]}});
  assert.equal(office.S.requests.get('r1').steps.s1, 'revise');
  assert.equal(office.S.requests.get('r1').phase, 'execute');
});

test('the 2.5D page closes its previous socket before opening a new one, as the 3D page does', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const open = /const open = \(\) => \{[\s\S]*?new WebSocket/.exec(page);
  assert.ok(open, 'open() found in index.html');
  assert.match(open[0], /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
  const live = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');
  assert.match(live, /if \(ws\) \{ ws\.onclose = null; ws\.onmessage = null; ws\.close\(\); \}/);
});
