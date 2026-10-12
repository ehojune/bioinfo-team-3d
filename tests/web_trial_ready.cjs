// Web trial readiness (2026-10-12): four defects a PI would hit in the first minutes of a trial.
//  1. a direct request showed "처리: 결정 중" forever, and the 3D row showed the raw English phase;
//  2. a research request waiting at CP1 showed 실행 as the current phase;
//  3. 보내기 sent a hidden-mode note for a selected direct request and got a 409;
//  4. a research request that ended without success showed "요청이 실패했어요: " with no reason.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));
const State = global.LabHQState;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const live3d = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');

// ---- 1. processing and phase labels, shared by the 2.5D board and the 3D request row ----
{
  const office = State.createOfficeState({ now: () => 100 });
  office.apply({ type: 'request.created', request_id: 'd', data: { text: '직접', mode: 'direct' } });
  const direct = office.S.requests.get('d');
  assert.equal(State.processingLabel(direct), '처리: 직접 맡김', 'a running direct request');
  office.apply({ type: 'request.completed', request_id: 'd', data: { ok: true, report: '끝' } });
  assert.equal(State.processingLabel(direct), '처리: 직접 맡김', 'and a finished one (it never gets a route decision)');
  assert.equal(office.phaseLabel(direct), '완료');

  office.apply({ type: 'request.created', request_id: 'o', data: { text: '조사', mode: 'orchestrate' } });
  const routed = office.S.requests.get('o');
  assert.equal(State.processingLabel(routed), '처리: 결정 중', 'a request still being routed');
  office.apply({ type: 'request.completed', request_id: 'o', data: { ok: false, error: '계획 실패', report: '계획 실패' } });
  assert.equal(State.processingLabel(routed), '', 'a request that failed before routing drops the label');
  assert.equal(State.processingLabel({ status: 'interrupted', mode: 'orchestrate' }), '', 'an interrupted request is not being decided either');
  assert.equal(State.processingLabel({ status: 'running', mode: 'orchestrate', processing: { mode: 'solo', agent_id: 'solo' } }), '처리: 단독(solo)');
  assert.equal(State.processingLabel({ status: 'running', mode: 'orchestrate', processing: { mode: 'team' } }), '처리: 팀');
  assert.equal(State.processingLabel({ status: 'running', mode: 'orchestrate', processing: { mode: 'team', fallback: true } }), '처리: 단독 실패 → 팀');
  assert.deepEqual(['briefing', 'plan', 'execute', 'review', 'report', 'done'].map(phase => office.phaseLabel({ phase })),
    ['브리핑', '계획', '실행', '리뷰', '보고', '완료']);

  assert.match(html, /const processingLabel = LabHQState\.processingLabel;/, '2.5D uses the shared label');
  assert.doesNotMatch(html, /'처리: 결정 중'/, 'the 2.5D page keeps no copy of the label table');
  assert.match(live3d, /LabHQState\.processingLabel\(q\)/, '3D uses the shared label');
  assert.match(live3d, /phaseLabel\(q\)/, '3D shows the Korean phase');
  assert.doesNotMatch(live3d, /\$\{q\.phase\}/, '3D never prints the internal phase key');
}

// ---- 2. the plan phase lasts until a step is dispatched ----
{
  const plan = { steps: [{ id: 's1', agent_id: 'analyst', depends_on: [] }] };
  const live = State.createOfficeState({ now: () => 100 });
  live.apply({ type: 'request.created', request_id: 'r', data: { text: '연구', mode: 'orchestrate' } });
  live.apply({ type: 'task.dispatched', request_id: 'r', agent_id: 'cso', task_id: 't0', data: { kind: 'plan' } });
  live.apply({ type: 'request.plan', request_id: 'r', data: plan });
  live.apply({ type: 'approval.requested', request_id: 'r', data: { id: 'cp1', kind: 'research_plan', summary: '계획 승인', request_id: 'r' } });
  live.apply({ type: 'request.status', request_id: 'r', data: { status: 'waiting_pi' } });
  const q = live.S.requests.get('r');
  assert.equal(q.status, 'waiting_pi');
  assert.equal(q.phase, 'plan', 'waiting for CP1 is 계획, not 실행');
  assert.deepEqual({ ...q.steps }, { s1: 'pending' });
  live.apply({ type: 'approval.resolved', request_id: 'r', data: { id: 'cp1', approved: true } });
  live.apply({ type: 'task.dispatched', request_id: 'r', agent_id: 'analyst', task_id: 't1', data: { kind: 'step', step_id: 's1' } });
  assert.equal(q.phase, 'execute', 'the first dispatched step starts 실행');
  assert.equal(q.steps.s1, 'working');

  // A re-plan goes back to 계획 until its first step runs.
  live.apply({ type: 'request.plan', request_id: 'r', data: plan });
  assert.equal(q.phase, 'plan');

  // A page opened during CP1 (snapshot only) reads the same.
  const reload = State.createOfficeState({ now: () => 100 });
  reload.apply({ type: 'snapshot', data: { agents: [], approvals: [{ id: 'cp1', kind: 'research_plan', request_id: 'r', summary: 's' }],
    requests: [{ id: 'r', status: 'waiting_pi', text: '연구', plan }] } });
  assert.equal(reload.S.requests.get('r').phase, 'plan', 'snapshot: a planned request with no step started is in 계획');
  const started = State.createOfficeState({ now: () => 100 });
  started.apply({ type: 'snapshot', data: { agents: [], requests: [
    { id: 'r', status: 'running', text: '연구', plan, step_status: { s1: 'working' } },
    { id: 'r2', status: 'running', text: '끝난 단계', plan, step_status: { s1: 'done' } },
    { id: 'r3', status: 'running', text: '아직', plan: { steps: [] } }] } });
  assert.equal(started.S.requests.get('r').phase, 'execute', 'snapshot: a started step is 실행');
  assert.equal(started.S.requests.get('r2').phase, 'execute');
  assert.equal(started.S.requests.get('r3').phase, 'briefing', 'no plan yet: still briefing');
  // The replayed plan event leaves 'plan'; a later step status in the snapshot still moves it on.
  const replay = State.createOfficeState({ now: () => 100 });
  replay.apply({ type: 'snapshot', data: { agents: [], requests: [{ id: 'r', status: 'running', text: '연구', plan, step_status: { s1: 'done' } }],
    recent_events: [{ type: 'request.created', request_id: 'r', data: { text: '연구', mode: 'orchestrate' } },
      { type: 'request.plan', request_id: 'r', data: plan }] } });
  assert.equal(replay.S.requests.get('r').phase, 'execute', 'snapshot: a replayed plan event with a started step is 실행');
}

// ---- 3. 보내기 with a selected direct request is a new request, never a note ----
async function sendTests() {
  const match = /async function send\(\) \{[\s\S]*?\r?\n\}\r?\n/.exec(html);
  assert.ok(match, 'send() found in index.html');
  async function run(current) {
    const calls = { note: [], request: [], toast: [] };
    const fields = { '#ask': { value: '새 요청이에요', focus() {} }, '#target': { value: '' }, '#project': { value: '' },
      '#send-mode': { value: 'note' }, '#team-only': { checked: false }, '#work-kind': { value: 'auto' } };
    const context = vm.createContext({
      $: selector => fields[selector], S: { current: 'cur', requests: new Map([['cur', { id: 'cur', ...current }]]) },
      api: { note: async (id, text) => { calls.note.push([id, text]); }, request: async body => { calls.request.push(body); } },
      toast: text => calls.toast.push(text), refs: { items: [], useDefaults: true }, nick: id => id,
      showRefs() {}, renderRefs() {},
    });
    vm.runInContext(`${match[0]}\nthis.send = send;`, context);
    await context.send();
    return calls;
  }
  let calls = await run({ mode: 'direct', status: 'running' });
  assert.deepEqual(calls.note, [], 'no hidden-mode note for a direct request (the gateway answers 409)');
  assert.equal(calls.request.length, 1, 'the text goes out as a new request');
  assert.equal(calls.request[0].mode, 'orchestrate');
  calls = await run({ mode: 'orchestrate', status: 'running' });
  assert.deepEqual(calls.note, [['cur', '새 요청이에요']], 'a running team request still takes the note');
  assert.deepEqual(calls.request, []);
}

// ---- 4. a request that ends without success never ends its line on a colon ----
{
  const failedLine = data => {
    const office = State.createOfficeState({ now: () => 100 });
    office.apply({ type: 'request.completed', request_id: 'r', data });
    return office.S.feed[0].text;
  };
  assert.equal(failedLine({ ok: false, error: '보고서는 나왔지만 claim 검사에서 문제가 있어요', report: '## 결론과 권고\nX' }),
    '요청이 실패했어요: 보고서는 나왔지만 claim 검사에서 문제가 있어요', 'the gateway reason is shown');
  assert.equal(failedLine({ ok: false, report: '## 결론과 권고\nX' }),
    '요청을 완료로 보지 않았어요. 보고서와 실행 기록에서 이유를 확인하세요', 'backstop with a report');
  assert.equal(failedLine({ ok: false, error: '   ', report: '' }), '요청이 끝까지 가지 못했어요. 실행 기록을 보세요', 'backstop with nothing');
  assert.equal(failedLine({ ok: true, report: 'X' }), '최종 보고서를 올렸어요');
  assert.doesNotMatch(failedLine({ ok: false, report: 'X' }), /: $/);
}

// ---- the 3D request row (live.js) shows the same labels ----
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.textContent = ''; this.dataset = {}; this.value = ''; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  insertBefore(child, ref) { this.children = this.children.filter(x => x !== child); this.children.splice(this.children.indexOf(ref), 0, child); return child; }
  removeChild(child) { this.children = this.children.filter(x => x !== child); return child; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector() { return null; }
}
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
global.document = { getElementById: element, createElement: tag => new Element(tag), querySelector: element, body: { classList: { add() {} } } };
global.location = { search: '?token=test-client', pathname: '/3d/', protocol: 'http:', host: 'example.invalid' };
global.localStorage = { getItem() { return null; }, setItem() {} };
global.history = { replaceState() {} };
global.setTimeout = () => 0; global.clearTimeout = () => {}; global.setInterval = () => 0;
const sockets = [];
global.WebSocket = class { constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); } send() {} close() {} };
const decide = fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8');
const decideUrl = 'data:text/javascript;base64,' + Buffer.from(decide).toString('base64');
const source = live3d.replace("'../../ui/decide.js'", `'${decideUrl}'`);
const rowLine = id => {
  const row = element('requests').children.find(node => node.dataset?.requestId === id);
  return row.children.find(node => node.tagName === 'P').textContent;
};

(async () => {
  await sendTests();
  const { startLiveOffice } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  startLiveOffice(() => {});
  const ws = sockets[0]; ws.readyState = 1; ws.onopen();
  let seq = 1;
  const event = ev => ws.onmessage({ data: JSON.stringify({ seq: ++seq, ...ev }) });
  const plan = { steps: [{ id: 's1', agent_id: 'analyst', depends_on: [], instruction: '세기' }] };
  event({ type: 'snapshot', data: { agents: [{ id: 'analyst' }], requests: [] } });

  event({ type: 'request.created', request_id: 'd', data: { text: '직접 일', mode: 'direct' } });
  assert.equal(rowLine('d'), '진행 중 · 실행 · 처리: 직접 맡김');
  event({ type: 'request.completed', request_id: 'd', data: { ok: true, report: '끝' } });
  assert.equal(rowLine('d'), '완료 · 처리: 직접 맡김', 'a finished direct request: no raw "done", no "결정 중"');

  event({ type: 'request.created', request_id: 'r', data: { text: '연구', mode: 'orchestrate' } });
  event({ type: 'request.plan', request_id: 'r', data: plan });
  event({ type: 'request.status', request_id: 'r', data: { status: 'waiting_pi' } });
  assert.equal(rowLine('r'), 'PI 결정 대기 · 계획 · 처리: 결정 중', 'waiting for CP1 is 계획 in 3D too');
  event({ type: 'request.completed', request_id: 'r', data: { ok: false, error: '계획을 승인하지 않았어요', report: 'x' } });
  assert.equal(rowLine('r'), '실패', 'failed before routing: the status alone, no phase and no label');
  console.log('trial readiness web tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
