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
assert.match(html, /id="runner-off"[^>]*hidden>러너 꺼짐/);
assert.match(html, /\$\('#runner-off'\)\.hidden = !\(MODE !== 'demo' && S\.conn === 'live' && S\.runnersKnown && !S\.runners\.size\)/);
assert.match(html, /cancelRequest: rid => post\(`\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/cancel`, \{\}\)/);
assert.match(html, /LabHQState\.isActiveRequest\(q\.status\) \|\| q\.status === 'interrupted'\) \? `<p class="req-actions"><button class="btn" id="req-cancel"/);
assert.equal(global.LabHQState.requestStatusLabel('waiting_pi'), 'PI 결정 대기');
assert.equal(global.LabHQState.requestStatusLabel('cancelled'), '취소됨');
console.log('request lifecycle web tests passed');
