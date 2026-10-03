const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const web = path.join(__dirname, '../labhq/web');
require(path.join(web, 'state.js'));

test('the shared reducer tracks follow-ups on a finished request', () => {
  const office = globalThis.LabHQState.createOfficeState({now: () => 10});
  const {S, apply} = office;
  apply({type: 'snapshot', data: {agents: [{id: 'cso', name: '부엉이 CSO'}], requests: [{id: 'r1', text: 't', status: 'done',
    cost_usd: 1, followups: [{id: 'fu_0', text: 'Which test?', status: 'done', answer: 'Wilcoxon'}]}]}});
  const q = S.requests.get('r1');
  assert.equal(q.followups.length, 1);
  apply({type: 'agent.usage', request_id: 'r1', data: {cost_usd: 0.25}});
  apply({type: 'request.followup', request_id: 'r1', ts: 11, data: {id: 'fu_1', text: 'And the effect?', agent_id: 'cso', status: 'running'}});
  assert.deepEqual(q.followups.map(f => [f.id, f.status]), [['fu_0', 'done'], ['fu_1', 'running']]);
  assert.match(S.feed[0].text, /이어 묻기: And the effect\?/);
  apply({type: 'request.followup_done', request_id: 'r1', ts: 12, data: {id: 'fu_1', ok: true, answer: 'd = 0.4', error: null, cost_usd: 1.25}});
  assert.equal(q.followups[1].status, 'done'); assert.equal(q.followups[1].answer, 'd = 0.4');
  assert.equal(q.status, 'done', 'the request stays finished');
  assert.equal(q.cost, 1.25); assert.equal(S.cost, 1.25, 'final total replaces the live follow-up subtotal');
  apply({type: 'request.followup_done', request_id: 'r1', data: {id: 'fu_2', ok: false, error: 'runner offline'}});
  assert.equal(q.followups[2].status, 'failed');
  assert.equal(Object.keys(q).includes('followups'), false, 'kept out of the legacy state digest');
});

test('the 2.5D request view asks follow-ups through the gateway', () => {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  assert.match(html, /followup: \(rid, text\) => post\(`\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/followup`, \{ text \}\)/);
  assert.match(html, /id="fu-text"/);
  assert.match(html, /id="fu-send"/);
  // #373: PI notes join the render key so a note event refreshes the same request card as follow-ups.
  assert.match(html, /q\.costKnown, q\.references, q\.followups, q\.piNotes\]/, 'a follow-up or note event re-renders the request view');
  // A draft belongs to its request: switching requests must not move it to another session (Codex review P2).
  assert.match(html, /id="fu-text" data-rid="\$\{esc\(q\.id\)\}"/);
  assert.match(html, /fuDrafts\.get\(q\.id\)/);
  assert.match(html, /rid = box\.dataset\.rid/);
  assert.doesNotMatch(html, /text = box\.value\.trim\(\), rid = S\.current/);
  const live = fs.readFileSync(path.join(web, 'lab3d/src/live.js'), 'utf8');
  assert.match(live, /q\.followups/);
});

test('a read-only run that changed files reaches the feed as an alert, unthrottled', () => {
  const {S, apply} = globalThis.LabHQState.createOfficeState({now: () => 10});
  apply({type: 'snapshot', data: {agents: [{id: 'cso', name: 'CSO'}], requests: []}});
  apply({type: 'agent.log', agent_id: 'cso', ts: 10, data: {text: '답을 찾는 중'}});
  apply({type: 'agent.log', agent_id: 'cso', ts: 11, data: {level: 'alert', text: '읽기 전용 실행이 파일을 바꿨습니다: + workdir/x'}});
  assert.equal(S.feed[0].cls, 'alert');
  assert.match(S.feed[0].text, /읽기 전용 실행이 파일을 바꿨습니다/);
  assert.equal(S.feed.length, 2, 'the alert is not swallowed by the 6 s talk throttle');
});
