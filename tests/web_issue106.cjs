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

const snapshot = running_tasks => ({ type: 'snapshot', data: {
  agents: [{ id: 'analyst' }], recent_events: [],
  requests: [{ id: 'r1', status: 'running', plan: { steps: [{ id: 's1', agent_id: 'analyst' }] }, step_status: {} }],
  running_tasks,
} });

(async () => {
  const office = global.LabHQState.createOfficeState({ now: () => 1000 });
  office.apply(snapshot([
    { id: 'new-running', request_id: 'r1', step_id: 's1', state: 'running', dispatched_at: 20 },
    { id: 'old-sleeping', request_id: 'r1', step_id: 's1', state: 'hibernating', dispatched_at: 10 },
  ]));
  assert.equal(office.S.requests.get('r1').steps.s1, 'working', 'running wins regardless of snapshot order');
  assert.equal(office.S.stepDetails.get('r1:s1').task_id, 'new-running', 'cancel targets the running task');

  const source = fs.readFileSync(path.join(root, 'labhq/web/ui/tasks.js'), 'utf8');
  const tasks = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  const board = new Element(); const cancelled = [];
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails,
    { nick: id => id, onCancel: id => cancelled.push(id) });
  const buttons = board.querySelectorAll('button');
  assert.equal(buttons.length, 1, 'running step keeps its cancel button');
  buttons[0].listeners.click[0]();
  assert.deepEqual(cancelled, ['new-running']);

  office.apply(snapshot([{ id: 'sleeping', request_id: 'r1', step_id: 's1', state: 'hibernating', dispatched_at: 30 }]));
  tasks.syncTaskBoard(board, office.S.requests.get('r1'), office.S.stepDetails, { nick: id => id });
  assert.ok(board.querySelectorAll('span').some(el => el.textContent === 'HPC 대기'), 'hibernating has a Korean task label');
  console.log('snapshot task priority, cancellation and hibernating label: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
