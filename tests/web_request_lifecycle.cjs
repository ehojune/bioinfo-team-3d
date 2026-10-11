// Request lifecycle in the web office (PI 점검 R5 R13 R15): new statuses, request cancel, runner banner.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
require(path.join(root, 'labhq/web/state.js'));

const office = global.LabHQState.createOfficeState({ now: () => 1000 });
const { S } = office;
office.apply({ type: 'snapshot', data: { agents: [], runners: [], requests: [
  { id: 'r1', status: 'running', text: 'QC', plan: { steps: [] }, step_status: {} },
  { id: 'r2', status: 'interrupted', text: 'old', plan: { steps: [] }, step_status: {} },
] } });
assert.equal(S.runnersKnown, true, 'a snapshot says whether any runner is connected');
assert.equal(S.runners.size, 0, 'no runner: the page shows 러너 꺼짐');
office.apply({ type: 'runner.online', data: { runner_id: 'lab-workstation' } });
assert.deepEqual([...S.runners], ['lab-workstation']);
office.apply({ type: 'runner.offline', data: { runner_id: 'lab-workstation' } });
assert.equal(S.runners.size, 0);
assert.ok(!Object.keys(S).includes('runners'), 'runner presence stays out of the legacy state digest');

office.apply({ type: 'request.status', request_id: 'r1', data: { status: 'waiting_pi' } });
assert.equal(S.requests.get('r1').status, 'waiting_pi');
assert.ok(global.LabHQState.isActiveRequest('waiting_pi'), 'a request waiting for the PI is still active');
office.apply({ type: 'request.runner_wait', request_id: 'r1', data: { message: '러너가 꺼져 있어 기다립니다.' } });
assert.ok(S.feed.some(item => item.text === '러너가 꺼져 있어 기다립니다.'));
office.apply({ type: 'request.status', request_id: 'r1', data: { status: 'running' } });
assert.equal(S.requests.get('r1').status, 'running');

office.apply({ type: 'request.completed', request_id: 'r1',
  data: { ok: false, status: 'cancelled', error: 'PI가 요청을 취소했습니다', report: '## 결론과 권고\n취소' } });
assert.equal(S.requests.get('r1').status, 'cancelled', 'a cancelled request is not shown as failed');
assert.ok(S.feed.some(item => item.text.startsWith('요청을 취소했어요')));
office.apply({ type: 'request.status', request_id: 'r1', data: { status: 'running' } });
assert.equal(S.requests.get('r1').status, 'cancelled', 'a late status event never reopens a finished request');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
assert.match(html, /id="runner-off"[^>]*hidden>러너 꺼짐 — 터미널에서 <code>labhq up<\/code>/);
assert.match(html, /\$\('#runner-off'\)\.hidden = !\(MODE !== 'demo' && S\.conn === 'live' && S\.runnersKnown && !S\.runners\.size\)/);
assert.match(html, /cancelRequest: rid => post\(`\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/cancel`, \{\}\)/);
assert.match(html, /LabHQState\.isActiveRequest\(q\.status\) \|\| q\.status === 'interrupted'\) \? `<p class="req-actions"><button class="btn" id="req-cancel"/);
assert.equal(global.LabHQState.requestStatusLabel('waiting_pi'), 'PI 결정 대기');
assert.equal(global.LabHQState.requestStatusLabel('cancelled'), '취소됨');

// #521: a lifted quota, login or facilities hold under an open PI card leaves the request waiting for the PI, as
// the gateway ranks holds (login > quota > facilities > PI card). The gateway sends no status event for that step.
function heldOffice() {
  const held = global.LabHQState.createOfficeState({ now: () => 1000 });
  held.apply({ type: 'snapshot', data: { agents: [], runners: [], requests: [
    { id: 'q1', status: 'running', text: 'DE', plan: { steps: [{ id: 's1' }, { id: 's2' }] }, step_status: {} }] } });
  return held;
}
const clarify = { type: 'approval.requested', request_id: 'q1', data: { id: 'c1', kind: 'clarify', summary: '분석군은?', request_id: 'q1' } };
const quotaWait = { type: 'request.step_quota_wait', request_id: 'q1', data: { step_id: 's2', engine: 'codex', resume_at: 2000 } };
const quotaBack = { type: 'request.step_quota_resumed', request_id: 'q1', data: { step_id: 's2', engine: 'codex' } };
for (const order of [[clarify, { type: 'request.status', request_id: 'q1', data: { status: 'waiting_pi' } }, quotaWait],
                     [quotaWait, clarify]]) {
  const held = heldOffice();
  order.forEach(ev => held.apply(ev));
  assert.equal(held.S.requests.get('q1').status, 'waiting_quota', 'the quota hold outranks the PI card');
  held.apply(quotaBack);
  assert.equal(held.S.requests.get('q1').status, 'waiting_pi', 'quota released under an open clarify card: PI 결정 대기, not 진행 중');
  assert.equal(global.LabHQState.requestStatusText(held.S.requests.get('q1'), held.S.approvals), 'PI 결정 대기');
}
const login = heldOffice();
[clarify, { type: 'request.step_login_wait', request_id: 'q1', data: { step_id: 's1', engine: 'claude' } },
 { type: 'request.step_login_resumed', request_id: 'q1', data: { step_id: 's1', engine: 'claude' } }].forEach(ev => login.apply(ev));
assert.equal(login.S.requests.get('q1').status, 'waiting_pi', 'the same after a login hold');
const tool = heldOffice();
[{ type: 'approval.requested', request_id: 'q1', data: { id: 't1', kind: 'tool_permission', summary: 'Bash', request_id: 'q1' } },
 quotaWait, quotaBack].forEach(ev => tool.apply(ev));
assert.equal(tool.S.requests.get('q1').status, 'running', 'a tool permission card does not hold the request');
const answered = heldOffice();
[clarify, quotaWait, { type: 'approval.resolved', request_id: 'q1', data: { id: 'c1', approved: true, note: 'a' } }, quotaBack]
  .forEach(ev => answered.apply(ev));
assert.equal(answered.S.requests.get('q1').status, 'running', 'an answered card holds nothing');

// #521: a card that timed out was not refused by the PI (R5); the feed says it timed out.
const expired = global.LabHQState.createOfficeState({ now: () => 1000 });
expired.apply({ type: 'snapshot', data: { agents: [], runners: [], requests: [
  { id: 'q2', status: 'interrupted', text: 'old', plan: { steps: [] }, step_status: {} }] } });
expired.apply({ type: 'approval.requested', request_id: 'q2', data: { id: 'rc', kind: 'resume', summary: '재개?', request_id: 'q2' } });
expired.apply({ type: 'approval.resolved', request_id: 'q2',
  data: { id: 'rc', approved: false, note: '재개 카드에 7일 동안 답이 없어 끝냈습니다', state: 'timed_out' } });
assert.notEqual(expired.S.feed[0].who, 'pi', 'nobody answered a timed-out card');
assert.match(expired.S.feed[0].text, /^시간 초과로 닫혔어요 \(재개 카드에 7일 동안 답이 없어/);
assert.ok(!expired.S.feed[0].text.includes('거절'), expired.S.feed[0].text);
expired.apply({ type: 'approval.requested', request_id: 'q2', data: { id: 'ex', kind: 'question', summary: '?', request_id: 'q2' } });
expired.apply({ type: 'approval.resolved', request_id: 'q2', data: { id: 'ex', approved: false, note: 'closed', state: 'expired' } });
assert.equal(expired.S.feed[0].text, '답 없이 닫혔어요 (closed)');
expired.apply({ type: 'approval.requested', request_id: 'q2', data: { id: 'no', kind: 'question', summary: '?', request_id: 'q2' } });
expired.apply({ type: 'approval.resolved', request_id: 'q2', data: { id: 'no', approved: false, note: '' } });
assert.deepEqual([expired.S.feed[0].who, expired.S.feed[0].text], ['pi', '거절했어요'], 'a real refusal still reads 거절');
console.log('request lifecycle web tests passed');
