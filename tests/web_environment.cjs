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

  office.apply(done(1, { ok: false, reason: 'exit 1: OSError: [Errno 28] No space left on device', environment }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.deepEqual(errors(), ['환경 문제: 디스크 공간이 부족합니다 — 작업 폴더 디스크를 비우세요',
    'exit 1: OSError: [Errno 28] No space left on device']);

  office.apply(done(2, { ok: true, reason: null }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.ok(!('environment' in office.S.stepDetails.get('r1:s1')), 'a later success clears the problem');
  assert.ok(!errors().some(text => text.startsWith('환경 문제')));

  office.apply(done(3, { ok: false, reason: 'exit 1: crashed' }));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.deepEqual(errors(), ['exit 1: crashed'], 'an ordinary failure shows only its error');
  console.log('environment failure web tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
