const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const web = path.join(__dirname, '../labhq/web');
const source = fs.readFileSync(path.join(web, 'ui/shutdown.js'), 'utf8');
const moduleReady = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.textContent = ''; }
  append(...els) { this.children.push(...els); }
  replaceChildren(...els) { this.children = els; }
  setAttribute() {}
  addEventListener(name, fn) { this.listeners[name] = fn; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
  remove() {}
  click() { if (!this.disabled) return this.listeners.click(); }
}
const nodes = el => [el, ...el.children.flatMap(nodes)];
async function office(data, mode = 'success') {
  const mod = await moduleReady, button = new Element('button'), body = new Element('body'), calls = [];
  let closed = false;
  const fetcher = async (url, options) => {
    calls.push({url, ...options});
    if (url.endsWith('readiness')) return {ok: true, json: async () => data};
    if (options.method === 'POST') {
      if (mode === 'lost-post') throw new TypeError('connection lost');
      return {ok: true, json: async () => ({status: 'stopping'})};
    }
    if (mode === 'success') throw new TypeError('connection refused');
    return {ok: mode !== 'auth', json: async () => mode === 'auth' ? {detail: 'bad client token'} : {status: 'failed'}};
  };
  mod.bindShutdown(button, {token: () => 'pi', doc: {body, createElement: tag => new Element(tag)},
    host: 'office.example:9000', fetcher, pause: async () => {}, closeTab: () => { closed = true; }});
  await button.click();
  const dialog = body.children[0];
  return {mod, dialog, calls, closed: () => closed,
    click: text => { const el = nodes(dialog).find(el => el.tag === 'button' && el.textContent === text); assert.ok(el, text); return el.click(); },
    texts: () => nodes(dialog).map(el => el.textContent).join('\n')};
}

test('all shutdown blocker categories have readable counts and titles', async () => {
  const {readinessText} = await moduleReady;
  assert.equal(readinessText({ready: true}), '요청이 모두 끝났습니다');
  const text = readinessText({ready: false, requests: [{status: 'running', text: '<분석>'}, {status: 'waiting_pi', text: '계획'}],
    followups: [{text: '질문'}], recruits: 2, finishing: [{text: '묶음'}]});
  for (const part of ['진행 중인 요청 1건 (<분석>)', 'PI 결정 대기 1건 (계획)', '이어 묻기 진행 중 1건', '채용 진행 중 2건', '마무리 중 1건']) assert.ok(text.includes(part));
  assert.throws(() => readinessText({}));
  // #658: a stuck bundle does not block and the age shows; the next start rebuilds it.
  const aged = readinessText({ready: false, finishing: [{text: '묶음', elapsed_s: 300}]});
  assert.ok(aged.includes('마무리 중 1건 (묶음 5분째)'));
  const staged = readinessText({ready: false, finishing: [{text: 'A', stage: 'cancel'}, {text: 'B', stage: 'bundle', elapsed_s: 300},
    {text: 'C', stage: 'result_storage'}, {text: 'D', stage: 'pi_interpretation'}]});
  for (const part of ['A · 취소 기록을 쓰는 중', 'B · 보고서 묶음 저장을 마무리하는 중 5분째', 'C · 결과 저장을 마무리하는 중', 'D · PI 해석을 마무리하는 중']) assert.ok(staged.includes(part), part);
  const stuck = readinessText({ready: true, finishing: [{text: '묶음', stuck: true}]});
  assert.ok(stuck.startsWith('요청이 모두 끝났습니다') && stuck.includes('멈춘 마무리 1건은 다음 시작 때 이어서 합니다'));
});
test('busy work needs a second explicit click; Enter cannot shut down', async () => {
  const ui = await office({ready: false, requests: [{status: 'waiting_pi', text: '계획'}]});
  await ui.click('labhq 끄기');
  assert.equal(ui.calls.length, 1);
  await ui.click('그래도 끄기');
  assert.ok(ui.texts().includes(ui.mod.FORCE_WARNING));
  let prevented = false;
  ui.dialog.listeners.keydown({key: 'Enter', preventDefault: () => {prevented = true;}, stopPropagation() {}});
  assert.ok(prevented); assert.equal(ui.calls.length, 1);
  await ui.click('진행 중인 일을 중단하고 labhq 끄기');
  assert.deepEqual(JSON.parse(ui.calls[1].body), {force: true, confirmation: 'interrupt-running-work'});
  assert.ok(ui.texts().includes(ui.mod.FINISHED));
});
test('idle shutdown names remote target and only finishes after accepted disconnect', async () => {
  const ui = await office({ready: true});
  assert.match(ui.texts(), /office.example:9000.*다른 PC/);
  await ui.click('labhq 끄기');
  assert.equal(ui.calls[1].headers.Authorization, 'Bearer pi');
  assert.ok(ui.texts().includes(ui.mod.FINISHED));
});
for (const mode of ['failed', 'auth', 'lost-post']) test(`${mode} never claims success`, async () => {
  const ui = await office({ready: true}, mode);
  await ui.click('labhq 끄기');
  assert.ok(!ui.texts().includes(ui.mod.FINISHED));
});
test('tab-only explains continued operation and never posts shutdown', async () => {
  const ui = await office({ready: true});
  await ui.click('탭만 닫기');
  assert.ok(ui.texts().includes(ui.mod.TAB_ONLY));
  await ui.click('이 탭 닫기');
  assert.ok(ui.closed()); assert.equal(ui.calls.length, 1);
});
test('both renderers bind the same shutdown control', () => {
  for (const name of ['index.html', 'lab3d/index.html']) assert.match(fs.readFileSync(path.join(web, name), 'utf8'), /id="shutdown"/);
  for (const name of ['index.html', 'lab3d/src/live.js']) assert.match(fs.readFileSync(path.join(web, name), 'utf8'), /bindShutdown\(/);
});
