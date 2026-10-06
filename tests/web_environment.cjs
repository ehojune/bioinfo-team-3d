// #35: an environment failure shows "환경 문제: cause — hint" on the task card, above the raw error.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
require(path.join(root, 'labhq/web/state.js'));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.textContent = ''; this.className = ''; this.listeners = {};
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, callback) { (this.listeners[type] ??= []).push(callback); }
  querySelectorAll(tag) {
    const found = [];
    const visit = node => { for (const child of node.children) { if (child.tagName === tag.toUpperCase()) found.push(child); visit(child); } };
    visit(this); return found;
  }
}
global.document = { createElement: tag => new Element(tag) };

const environment = { id: 'disk_full', cause: '디스크 공간이 부족합니다', hint: '작업 폴더 디스크를 비우세요', source: 'error' };
const done = (seq, data) => ({ type: 'request.step_done', seq, ts: 1, request_id: 'r1', data: { step_id: 's1', agent_id: 'analyst', ...data } });

(async () => {
  const office = global.LabHQState.createOfficeState({ now: () => 1000 });
  office.apply({ type: 'snapshot', data: {
    agents: [{ id: 'analyst' }], recent_events: [], running_tasks: [],
    requests: [{ id: 'r1', status: 'running', plan: { steps: [{ id: 's1', agent_id: 'analyst' }] }, step_status: {} }],
  } });
  const source = fs.readFileSync(path.join(root, 'labhq/web/ui/tasks.js'), 'utf8');
  const tasks = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  const board = new Element();
  const errors = () => board.querySelectorAll('p').filter(el => el.className.includes('task-error')).map(el => el.textContent);

  const card = { id: 'appr-fix', kind: 'facilities_fix', summary: 's1 환경 문제를 고칠까요?', request_id: 'r1',
    detail: { step_id: 's1', action: '작업 폴더 캐시 비우기', reason: '디스크 공간 부족',
      signature_id: 'disk_full', command: '작업 폴더 안 cache 비우기' } };
  office.apply({ type: 'approval.requested', seq: 1, ts: 1, request_id: 'r1', data: card });
  assert.equal(office.S.requests.get('r1').status, 'waiting_facilities_fix');
  assert.equal(office.S.approvals.get('appr-fix').detail.signature_id, 'disk_full');
  office.apply({ type: 'approval.resolved', seq: 2, ts: 1, request_id: 'r1', data: { id: 'appr-fix', approved: true } });
  assert.equal(office.S.requests.get('r1').status, 'running');
  office.apply({ type: 'request.facilities_fix', seq: 3, ts: 1, request_id: 'r1', data: {
    step_id: 's1', ok: null, status: 'applied', fix_id: 'install_python_package', action: 'Python 패키지 설치 지시' } });
  assert.ok(office.S.feed.some(row => row.text.includes('환경 수정 적용')));

  office.apply(done(1, { ok: false, reason: 'exit 1: OSError: [Errno 28] No space left on device', environment }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.deepEqual(errors(), ['환경 문제: 디스크 공간이 부족합니다 — 작업 폴더 디스크를 비우세요',
    'exit 1: OSError: [Errno 28] No space left on device']);

  const applied = { ok: null, status: 'applied', fix_id: 'install_python_package', action: 'Python 패키지 설치 지시' };
  office.apply(done(2, { ok: false, reason: 'exit 1: other error', facilities_fix: applied }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.ok(board.querySelectorAll('p').some(el => el.textContent.includes('환경 수정 적용')));

  const fixed = { ok: true, status: 'succeeded', fix_id: 'install_python_package', action: 'Python 패키지 설치 지시' };
  office.apply({ type: 'request.facilities_fix', seq: 4, ts: 1, request_id: 'r1', data: { step_id: 's1', ...fixed } });
  assert.ok(office.S.feed.some(row => row.text.includes('환경 수정 성공')));
  office.apply(done(2, { ok: true, reason: null, facilities_fix: fixed }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.ok(!('environment' in office.S.stepDetails.get('r1:s1')), 'a later success clears the problem');
  assert.ok(!errors().some(text => text.startsWith('환경 문제')));
  assert.ok(board.querySelectorAll('p').some(el => el.textContent.includes('환경 수정 성공')));

  office.apply(done(3, { ok: false, reason: 'exit 1: crashed' }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.deepEqual(errors(), ['exit 1: crashed'], 'an ordinary failure shows only its error');
  office.apply({ type: 'request.facilities_fix', seq: 5, ts: 1, request_id: 'r1', data: {
    step_id: 's1', ok: false, status: 'failed', fix_id: 'install_python_package', error: '같은 오류가 다시 발생' } });
  assert.ok(office.S.feed.some(row => row.text.includes('환경 수정 실패')));
  console.log('environment failure web tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
