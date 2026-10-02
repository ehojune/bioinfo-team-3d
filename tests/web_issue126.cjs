// #126: a snapshot carries the head of a long follow-up answer; both offices open the rest on demand.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));

const HEAD = 'A'.repeat(2000), FULL = HEAD + 'B'.repeat(18000);
const REPORT_HEAD = 'R'.repeat(2000), FULL_REPORT = REPORT_HEAD + 'S'.repeat(18000);
const snapshot = {type: 'snapshot', seq: 5, data: {agents: [{id: 'cso'}], approvals: [], requests: [{id: 'r1', text: 't', status: 'done',
  followups: [{id: 'fu_long', text: 'Long?', status: 'done', answer: HEAD, answer_truncated: true, answer_chars: FULL.length},
              {id: 'fu_short', text: 'Short?', status: 'done', answer: 'short'}]}],
  recent_events: [{type: 'request.completed', request_id: 'r1', data: {ok: true, report: REPORT_HEAD,
    report_truncated: true, report_chars: FULL_REPORT.length}},
    {type: 'request.followup_done', request_id: 'r1', data: {id: 'fu_long', ok: true, answer: HEAD,
      answer_truncated: true, answer_chars: FULL.length}}]}};
const detail = {id: 'r1', report: FULL_REPORT,
  followups: [{id: 'fu_long', answer: FULL}, {id: 'fu_short', answer: 'short'}]};

// Shared reducer: the truncation mark survives the snapshot and the full request replaces it.
const office = global.LabHQState.createOfficeState({now: () => 10});
office.apply(snapshot);
const shown = office.S.requests.get('r1').followups;
assert.deepEqual(shown.map(f => [f.id, f.answer.length, !!f.answer_truncated]), [['fu_long', 2000, true], ['fu_short', 5, false]]);
assert.equal(office.fillFollowups('missing', detail), 0);
assert.equal(office.fillFollowups('r1', {followups: [{id: 'fu_long'}]}), 0, 'a request without the answer changes nothing');
assert.equal(office.fillFollowups('r1', detail), 1);
const filled = office.S.requests.get('r1').followups;
assert.equal(filled[0].answer, FULL); assert.equal('answer_truncated' in filled[0], false);
assert.equal(filled[1], shown[1], 'untouched entries keep their identity');
assert.equal(office.fillFollowups('r1', detail), 0, 'filling twice is a no-op');
assert.equal(office.S.requests.get('r1').report, REPORT_HEAD);
assert.equal(office.S.requests.get('r1').report_truncated, true);
assert.equal(office.fillRequestDetail('r1', detail), 1, 'the full terminal report is filled on demand');
assert.equal(office.S.requests.get('r1').report, FULL_REPORT);
assert.equal('report_truncated' in office.S.requests.get('r1'), false);
const live = global.LabHQState.createOfficeState({now: () => 10});
live.apply({type: 'request.followup_done', request_id: 'r2', data: {id: 'fu', ok: true, answer: FULL}});
assert.equal('answer_truncated' in live.S.requests.get('r2').followups[0], false, 'a live answer arrives whole');

// 2.5D: the request view offers 전문 보기 and loads GET /api/requests/{id}.
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
assert.match(html, /requestDetail: rid => get\(`\/api\/requests\/\$\{encodeURIComponent\(rid\)\}`\)/);
assert.match(html, /class="btn fu-full"/);
assert.match(html, /fillRequestDetail\(rid, await api\.requestDetail\(rid\)\)/);
assert.match(html, /\$\{f\.answer_truncated \? '…' : ''\}<\/pre>\$\{fullAnswerHTML\(q, f\)\}/);
assert.match(html, /\[\.\.\.f\.answer\]\.length\.toLocaleString\(\)/, 'shown answer length uses Unicode code points');
assert.match(html, /class="btn report-full"/, 'a shortened terminal report can be opened on demand');

// 3D: a small DOM/transport harness clicks 전문 보기 and checks the fetch and the shown answer.
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.textContent = ''; this.dataset = {}; this.value = ''; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector() { return null; }
}
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const all = node => [node, ...node.children.flatMap(all)];
global.document = {getElementById: element, createElement: tag => new Element(tag), querySelector: element, body: {classList: {add() {}}}};
global.location = {search: '?token=test-client', pathname: '/3d/', protocol: 'http:', host: 'example.invalid'};
global.localStorage = {getItem() { return null; }, setItem() {}};
global.history = {replaceState() {}};
global.setTimeout = () => 0; global.clearTimeout = () => {};
const intervals = []; global.setInterval = callback => { intervals.push(callback); return 0; };
const sockets = [];
global.WebSocket = class { constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); } send() {} close() {} };
const fetched = [];
global.fetch = async (url, options) => { fetched.push([url, options.headers.Authorization]); return {ok: true, json: async () => detail}; };
const decide = fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8');
const decideUrl = 'data:text/javascript;base64,' + Buffer.from(decide).toString('base64');
const source = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8').replace("'../../ui/decide.js'", `'${decideUrl}'`);
(async () => {
  const {startLiveOffice} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  startLiveOffice(() => {});
  const ws = sockets[0]; ws.readyState = 1; ws.onopen();
  ws.onmessage({data: JSON.stringify(snapshot)});
  const nodes = () => all(element('requests'));
  const line = () => nodes().find(n => n.tagName === 'P' && n.textContent.startsWith('이어 묻기: Long?')).textContent;
  assert.equal(line(), `이어 묻기: Long? → ${HEAD}…`);
  const buttons = nodes().filter(n => n.tagName === 'BUTTON' && n.textContent === '전문 보기');
  assert.equal(buttons.length, 1, 'only the shortened answer offers 전문 보기');
  intervals[0]();
  assert.equal(nodes().find(n => n.tagName === 'BUTTON' && n.textContent === '전문 보기'), buttons[0],
    'the approval clock render keeps the request button node and its in-progress click');
  await buttons[0].onclick();
  assert.deepEqual(fetched, [['/api/requests/r1', 'Bearer test-client']]);
  assert.equal(line(), `이어 묻기: Long? → ${FULL}`);
  assert.equal(nodes().some(n => n.tagName === 'BUTTON' && n.textContent === '전문 보기'), false);
  console.log('snapshot answer head, full answer on demand in 2.5D and 3D: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
