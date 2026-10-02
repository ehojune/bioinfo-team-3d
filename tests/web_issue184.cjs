// #184: approval notices end with their approval, 3D name tags sit over their own heads, and the 2.5D
// staff strip has its own band on a desktop instead of covering the floor and the messenger.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {test} = require('node:test');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

const json = value => JSON.parse(JSON.stringify(value));
const ask = id => ({type: 'approval.requested', agent_id: 'cso', data: {id, kind: 'tool_permission', summary: `Example ${id}`}});
const ENDINGS = [['approval.resolved', {approved: true}], ['approval.resolved', {approved: false, note: 'no'}],
  ['approval.resolved', {approved: false, note: 'timed out', state: 'timed_out'}], ['approval.expired', {}], ['approval.stale', {}]];

// A small DOM/transport harness for the 3D live panel, as in web_live.cjs.
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.textContent = ''; this.dataset = {}; this.value = ''; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  insertBefore(child, ref) { this.removeChild(child); this.children.splice(this.children.indexOf(ref), 0, child); return child; }
  removeChild(child) { this.children = this.children.filter(x => x !== child); return child; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector() { return null; }
}
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
global.document = {getElementById: element, createElement: tag => new Element(tag), querySelector: element, body: {classList: {add() {}}}};
global.location = {search: '?token=test-client', pathname: '/3d/', protocol: 'http:', host: 'example.invalid'};
global.localStorage = {getItem() { return null; }, setItem() {}};
global.history = {replaceState() {}};
global.setTimeout = () => 0; global.clearTimeout = () => {}; global.setInterval = () => 0;
const sockets = [];
global.WebSocket = class { constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); } send() {} close() {} };

// The real skins with the vendored three: bare `three` specifiers become file URLs in a temporary copy.
async function importSkins() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'labhq-184-'));
  const three = pathToFileURL(path.join(root, 'vendor/three/build/three.module.js')).href;
  const addons = pathToFileURL(path.join(root, 'vendor/three/examples/jsm')).href;
  try {
    fs.writeFileSync(path.join(dir, 'package.json'), '{"type":"module"}');
    for (const name of ['primitives.js', 'characters.js', 'skins.js']) {
      const source = fs.readFileSync(path.join(root, 'lab3d/src', name), 'utf8')
        .replace(/from 'three'/g, `from '${three}'`).replace(/from 'three\/addons\/([^']+)'/g, `from '${addons}/$1'`);
      fs.writeFileSync(path.join(dir, name), source);
    }
    const load = name => import(pathToFileURL(path.join(dir, name)).href);
    return {THREE: await import(three), ...(await load('primitives.js')), ...(await load('characters.js')), ...(await load('skins.js'))};
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
}

test('every way an approval ends clears its notice in the reducer, 3D and 2.5D', async () => {
  const office = global.LabHQState.createOfficeState({now: () => 10});
  assert.deepEqual(json(office.apply(ask('a1'))), [{type: 'toast', text: '승인 요청이 왔어요: Example a1', approval_id: 'a1'}]);
  for (const [end, data] of ENDINGS) {
    office.apply(ask('a1'));
    assert.deepEqual(json(office.apply({type: end, data: {id: 'a1', ...data}})), [{type: 'toast.clear', approval_id: 'a1'}], end);
    assert.equal(office.S.approvals.has('a1'), false, `${end} removes the approval`);
  }
  office.apply(ask('a2')); office.apply(ask('a3'));
  const cleared = office.apply({type: 'snapshot', data: {agents: [], requests: [], approvals: [{id: 'a3', kind: 'budget', summary: 'kept'}]}});
  assert.deepEqual(json(cleared), [{type: 'toast.clear', approval_id: 'a2'}], 'a snapshot clears approvals decided while away');

  const decide = fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8');
  const decideUrl = 'data:text/javascript;base64,' + Buffer.from(decide).toString('base64');
  const source = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8').replace("'../../ui/decide.js'", `'${decideUrl}'`);
  const {startLiveOffice} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  startLiveOffice(() => {});
  const ws = sockets[0]; ws.readyState = 1; ws.onopen();
  let seq = 0;
  const event = ev => ws.onmessage({data: JSON.stringify({seq: ++seq, ...ev})});
  const notice = () => element('live-notice').textContent;
  event({type: 'snapshot', data: {agents: [{id: 'cso'}], requests: [], approvals: []}});
  for (const [end, data] of ENDINGS.slice(0, 4)) {
    event(ask('b1'));
    assert.equal(notice(), '승인 요청이 왔어요: Example b1');
    event({type: end, data: {id: 'b1', ...data}});
    assert.equal(notice(), '', `${end} clears the 3D notice`);
  }
  event(ask('b2')); event(ask('b3'));
  event({type: 'approval.resolved', data: {id: 'b2', approved: true}});
  assert.equal(notice(), '승인 요청이 왔어요: Example b3', 'an older approval ending keeps the newer notice');
  event({type: 'approval.stale', data: {id: 'b3'}});
  assert.equal(notice(), '이미 끝난 승인 요청입니다.');
  event(ask('b4'));
  event({type: 'snapshot', data: {agents: [{id: 'cso'}], requests: [], approvals: []}});
  assert.equal(notice(), '', 'reconnecting after the decision clears it too');

  // 2.5D hides its toast on the same effect.
  assert.match(html, /effect\.type === 'toast'\) toast\(effect\.text, effect\.approval_id\)/);
  assert.match(html, /effect\.type === 'toast\.clear' && toastApproval && toastApproval === effect\.approval_id\) hideToast\(\)/);
});

test('3D name tags sit over their own heads from every allowed camera angle', async () => {
  const {THREE, Shapes, ROSTER, buildProcedural, labelPoint} = await importSkins();
  assert.equal(typeof labelPoint, 'function', 'skins expose one shared tag point');
  const scene = new THREE.Scene(), shapes = new Shapes(scene), parent = shapes.group(scene);
  const chars = ROSTER.map((def, index) => ({def, skin: buildProcedural({shapes, parent, def, index})}));
  scene.updateMatrixWorld(true);
  const main = fs.readFileSync(path.join(root, 'lab3d/src/main.js'), 'utf8');
  assert.match(main, /else labelPoint\(ch\.skin,projection\);/, 'the office view places tags with labelPoint');
  assert.match(main, /new THREE\.Vector3\(0,\.68,\.1\)/, 'the test looks at the office view target');
  const target = new THREE.Vector3(0, .68, .1), camera = new THREE.OrthographicCamera(-12, 12, 9, -9, .1, 160);
  const view = (point, phi, theta) => {
    camera.position.copy(target).add(new THREE.Vector3().setFromSpherical(new THREE.Spherical(35, phi, theta)));
    camera.lookAt(target); camera.updateMatrixWorld(true);
    return point.clone().applyMatrix4(camera.matrixWorldInverse);
  };
  const heads = chars.map(c => c.skin.anchors.head.getWorldPosition(new THREE.Vector3()));
  const nearest = (point, phi, theta) => {
    const p = view(point, phi, theta);
    return heads.map((h, i) => [view(h, phi, theta).sub(p).setZ(0).length(), i]).sort((a, b) => a[0] - b[0])[0][1];
  };
  // The default camera direction (13,20,25) and the corners OrbitControls allows (polar .57-.98, azimuth .08-.73).
  const views = [[Math.acos(20 / Math.hypot(13, 20, 25)), Math.atan2(13, 25)], [.57, .08], [.57, .73], [.98, .08], [.98, .73]];
  for (const [phi, theta] of views) {
    chars.forEach((c, i) => {
      const tag = labelPoint(c.skin), head = heads[i];
      assert.ok(view(tag, phi, theta).y > view(head, phi, theta).y + .3, `${c.def.id}: tag above its head`);
      assert.equal(nearest(tag, phi, theta), i, `${c.def.id}: the closest head on screen is its own`);
    });
  }
  // The old desk-front anchor reads as another colleague's name: the check above would catch it.
  const deskFront = chars.map(c => c.skin.root.localToWorld(new THREE.Vector3(0, .60, 1.60)));
  assert.ok(deskFront.some((p, i) => nearest(p, ...views[0]) !== i), 'desk-front tags sit nearer another head');
  const css = fs.readFileSync(path.join(root, 'lab3d/index.html'), 'utf8');
  assert.match(css, /\.tag\{position:absolute;pointer-events:none;text-align:center;transform:translate\(-50%,-100%\)/,
    'an office tag grows upward from its point');
  assert.match(css, /\.tag\.gallery\{padding-top:6px;transform:translate\(-50%,0\)\}/, 'a gallery tag stays under its body');
});

test('2.5D desktop keeps office, Command Center, staff strip and composer in separate bands', () => {
  // Layout itself needs a browser; this pins the rules measured at 1200x750 (see the PR) so they are not lost.
  const desktop = /@media \(min-width:1000px\)\{\/\* #184[^\n]*\n([\s\S]*?)\n\}/.exec(html);
  assert.ok(desktop, 'one desktop block owns the fitted layout');
  for (const rule of ['body{height:100vh;height:100dvh;min-height:560px;display:flex;flex-direction:column;padding-bottom:0}',
    '.layout{flex:1 1 auto;min-height:0;width:100%;grid-template-rows:minmax(0,1fr)}', '.office{max-height:100%;overflow:auto}',
    '.panels{max-height:100%}', '.staff-wrap{position:static;flex:none;width:100%}', '.composer{position:relative;flex:none}']) {
    assert.ok(desktop[1].includes(rule), `desktop layout keeps ${rule}`);
  }
});
