// #57 row 1: a new or finished approval must not take away the PI's half-written answer. The decision box
// keeps the typed text, the picked option and the keyboard focus in both the 2.5D page and the 3D panel.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));

// A DOM that behaves like a browser where it matters here: a node has one parent, moving or re-inserting it
// detaches it first, and detaching the focused element sends focus back to <body> (HTML focus fixup).
const doc = {activeElement: null};
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.dataset = {};
    this.textContent = ''; this.value = ''; this.className = ''; this.hidden = false; this.listeners = {}; this.detached = 0;
  }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  get isConnected() { return doc.body.contains(this); }
  _detach() {
    const parent = this.parentNode;
    if (!parent) return;
    parent.children.splice(parent.children.indexOf(this), 1);
    this.parentNode = null; this.detached += 1;
    if (this.contains(doc.activeElement)) doc.activeElement = doc.body;
  }
  append(...nodes) { for (const node of nodes) { node._detach(); node.parentNode = this; this.children.push(node); } }
  insertBefore(node, ref) {
    if (ref == null) { this.append(node); return node; }
    node._detach();
    this.children.splice(this.children.indexOf(ref), 0, node); node.parentNode = this;
    return node;
  }
  replaceChildren(...nodes) { for (const child of [...this.children]) child._detach(); this.append(...nodes); }
  removeChild(node) { if (node.parentNode === this) node._detach(); return node; }
  remove() { this._detach(); }
  focus() { if (this.isConnected) doc.activeElement = this; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector() { return null; }
  get classList() { return {add() {}, remove() {}, toggle() {}}; }
}
doc.body = new Element('body');
doc.activeElement = doc.body;
doc.createElement = tag => new Element(tag);
const ids = new Map();
doc.getElementById = id => {
  if (!ids.has(id)) { const el = new Element(); doc.body.append(el); ids.set(id, el); }
  return ids.get(id);
};
doc.querySelector = doc.getElementById;
global.document = doc;

const rowsOf = container => container.children[0]?.children || [];
const keys = container => rowsOf(container).map(row => row.dataset.decisionKey);
const clarify = (id, created_at) => ({id, kind: 'clarify', agent_id: 'cso', summary: `질문 ${id}`, created_at,
  detail: {questions: [{question: '어느 코호트로 할까요?', options: ['cases', 'controls']}]}});
const budget = (id, created_at) => ({id, kind: 'budget', agent_id: 'cso', summary: `예산 ${id}`, created_at});

// The PI is midway through answering: one option picked, free text and a memo typed, cursor in the memo.
function startAnswer(row) {
  const p = row._decisionParts, answer = p.questions._answers[0];
  p.questions.children[0].children.find(el => el.className === 'clarify-options').children[1].onclick();
  answer.free.value = 'donors > 3 only';
  p.note.focus(); p.note.value = '쓰던 메모';
  assert.equal(doc.activeElement, p.note);
  return {p, answer, before: row.detached};
}
function assertKept(row, {p, answer, before}, label) {
  assert.equal(p.note.value, '쓰던 메모', `${label}: memo text kept`);
  assert.equal(answer.free.value, 'donors > 3 only', `${label}: free answer kept`);
  assert.equal(answer.choice, 1, `${label}: picked option kept`);
  assert.equal(doc.activeElement, p.note, `${label}: keyboard focus kept`);
  assert.equal(row.detached, before, `${label}: the card being answered stays in place`);
}

test('2.5D decision box keeps the answer while approvals arrive and finish', async () => {
  const decide = await import('data:text/javascript;base64,' +
    Buffer.from(fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8')).toString('base64'));
  const container = doc.getElementById('approvals-2d');
  const options = {now: () => 100, nick: id => id, avatarHTML: () => '<svg></svg>'};
  const sync = (approvals, suggestions = []) => decide.syncDecisionCards(container, approvals, suggestions, options);
  const a1 = clarify('a1', 10);
  sync([a1]);
  const row = rowsOf(container)[0], state = startAnswer(row);

  sync([a1]);  // the 15-second clock and every agent event re-render the box
  assertKept(row, state, 'unchanged render');
  sync([a1, budget('a2', 30)]);
  assert.deepEqual(keys(container), ['approval:a2', 'approval:a1'], 'a newer approval goes on top');
  assertKept(row, state, 'newer approval arrives');
  sync([a1, budget('a2', 30), budget('a0', 5)], [{id: 's1', repo: 'example/tool', created_at: 40}]);
  assert.deepEqual(keys(container), ['suggestion:s1', 'approval:a2', 'approval:a1', 'approval:a0']);
  assertKept(row, state, 'older approval and a suggestion arrive');
  const a2 = rowsOf(container)[1];
  sync([a1, budget('a0', 5)], [{id: 's1', repo: 'example/tool', created_at: 40}]);
  assert.deepEqual(keys(container), ['suggestion:s1', 'approval:a1', 'approval:a0'], 'a finished approval leaves');
  assert.equal(a2.parentNode, null);
  assertKept(row, state, 'another approval finishes');
  sync([a1]);
  assertKept(row, state, 'the rest finish');
  assert.equal(decide.decisionNote(row, true), 'Q1. b) controls — donors > 3 only\n메모: 쓰던 메모');

  sync([]);
  assert.equal(container.children[0].className, 'empty-note');
  sync([budget('a3', 50)]);
  assert.deepEqual(keys(container), ['approval:a3'], 'the box fills again after it was empty');
});

test('3D decision panel keeps the answer through live events and its one-second clock', async () => {
  global.location = {search: '?token=test-client', pathname: '/3d/', protocol: 'http:', host: 'example.invalid'};
  global.localStorage = {getItem() { return null; }, setItem() {}};
  global.history = {replaceState() {}};
  const intervals = [];
  global.setTimeout = () => 0; global.clearTimeout = () => {};
  global.setInterval = callback => { intervals.push(callback); return intervals.length; };
  const sockets = [];
  global.WebSocket = class {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(message) { this.sent.push(JSON.parse(message)); }
    close() {}
  };
  const decideUrl = 'data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8')).toString('base64');
  const source = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8').replace("'../../ui/decide.js'", `'${decideUrl}'`);
  const {startLiveOffice} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  startLiveOffice(() => {});
  const ws = sockets[0]; ws.readyState = 1; ws.onopen();
  let seq = 0;
  const event = (type, data) => ws.onmessage({data: JSON.stringify({type, seq: ++seq, ts: 1, data})});
  event('snapshot', {agents: [{id: 'cso'}], requests: [], approvals: []});
  const container = doc.getElementById('approvals');
  event('approval.requested', clarify('b1', 10));
  const row = rowsOf(container)[0], state = startAnswer(row);

  intervals[0]();
  assertKept(row, state, '3D clock tick');
  event('approval.requested', budget('b2', 30));
  assert.deepEqual(keys(container), ['approval:b2', 'approval:b1']);
  assertKept(row, state, '3D newer approval arrives');
  event('approval.resolved', {id: 'b2', approved: true});
  assert.deepEqual(keys(container), ['approval:b1']);
  assertKept(row, state, '3D other approval finishes');
  intervals[0]();
  assertKept(row, state, '3D clock tick after a change');

  // The same card input answers in 3D; no browser prompt() is involved.
  state.p.approve.onclick();
  assert.deepEqual(ws.sent.at(-1), {type: 'approval.resolve', id: 'b1', approved: true,
    note: 'Q1. b) controls — donors > 3 only\n메모: 쓰던 메모'});
});
