const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../labhq/web');
require(path.join(root, 'state.js'));
const state = globalThis.LabHQState;
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

test('2.5D gateway demo uses live transport, demo header, countdown and separate storage', () => {
  const data = new Map([['labhq_token', 'live-token']]);
  const localStorage = {getItem:k => data.get(k), setItem:(k,v) => data.set(k,v)};
  const mode = html.match(/const MODE = .*;/)[0];
  const store = html.slice(html.indexOf('const store = {'), html.indexOf('const raf ='));
  const header = html.slice(html.indexOf('function renderHeader()'), html.indexOf('const ZONES ='));
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {lastChild:{textContent:''}, classList:{toggle() {}}});
    return nodes.get(id);
  };
  for (const demo of [false, true, false]) {
    const {S, apply} = state.createOfficeState(); S.conn = 'live';
    apply({type:'demo.status', data:{phase:'waiting', remaining_s:7}});
    const ctx = vm.createContext({BOOT:{mode:'live', demo}, QS:new URLSearchParams(),
      LabHQState:state, localStorage, $, S, snapshotLoaded:true, INSTANCE:'', document:{},
      loadingText:() => ''});
    vm.runInContext(`${mode}\nconst IS_DEMO = BOOT.demo === true || MODE === 'demo';\n${store}\n${header}
      globalThis.mode = MODE; globalThis.saved = store.get('labhq_token');
      if (IS_DEMO) store.set('labhq_token', 'demo-token'); renderHeader();`, ctx);
    assert.equal(ctx.mode, 'live', 'gateway mock demo must keep WebSocket transport');
    assert.equal(ctx.saved, demo ? undefined : 'live-token');
    assert.equal($('#conn').lastChild.textContent, demo ? '데모(mock)' : '실시간');
    assert.equal($('#note').hidden, !demo);
    if (demo) assert.match($('#note').textContent, /labhq up.*다음 데모 요청까지 7초/);
  }
  assert.equal(data.get('labhq_token'), 'live-token');
  assert.equal(data.get('labhq_demo:labhq_token'), 'demo-token');
  const office = state.createOfficeState();
  office.apply({type:'snapshot', data:{demo_status:{phase:'waiting', remaining_s:4},
    recent_events:[{type:'demo.status', data:{phase:'waiting', remaining_s:8}}]}});
  assert.match(state.demoBannerText(office.S.demoStatus), /4초/);
  office.apply({type:'snapshot', data:{requests:[], approvals:[]}});
  assert.equal(office.S.demoStatus, null);
  assert.equal(office.S.requests.size, 0);
  assert.equal(office.S.approvals.size, 0);
});

test('3D demo labels and token storage stay isolated on the same browser origin', async () => {
  class Element {
    constructor() {this.children=[]; this.dataset={}; this.textContent=''; this.listeners={}; this.value='';}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children=children;}
    addEventListener(k,f) {this.listeners[k]=f;}
  }
  const elements = new Map(), data = new Map([['labhq_token', 'live-token']]), sockets = [];
  const element = id => {if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id);};
  global.document = {getElementById:element, querySelector:element, createElement:() => new Element(),
    body:{classList:{add(){}}}};
  global.localStorage = {getItem:k => data.get(k), setItem:(k,v) => data.set(k,v)};
  global.history = {replaceState(){}};
  global.location = {search:'?token=demo-token', pathname:'/3d/', protocol:'http:', host:'example.invalid:43210'};
  global.WebSocket = class {constructor(url) {this.url=url; sockets.push(this);} close(){} };
  const originalInterval = global.setInterval;
  global.setInterval = () => 0;
  const decide = fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const url = 'data:text/javascript;base64,' + Buffer.from(decide).toString('base64');
  const source = fs.readFileSync(path.join(root, 'lab3d/src/live.js'),'utf8').replace("'../../ui/decide.js'", `'${url}'`);
  try {
    const {startLiveOffice} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
    global.LABHQ_BOOT = {mode:'live', demo:true};
    startLiveOffice(() => {});
    let ws = sockets.at(-1); ws.onopen();
    assert.equal(element('connection').textContent, '데모(mock)');
    assert.equal(element('demo-banner').hidden, false);
    ws.onmessage({data:JSON.stringify({type:'snapshot', data:{demo_status:{phase:'waiting', remaining_s:6}}})});
    assert.match(element('demo-banner').textContent, /labhq up.*6초/);
    assert.equal(data.get('labhq_token'), 'live-token');
    assert.equal(data.get('labhq_demo:labhq_token'), 'demo-token');
    global.LABHQ_BOOT = {mode:'live'}; global.location.search = '';
    startLiveOffice(() => {});
    ws = sockets.at(-1); ws.onopen();
    assert.match(ws.url, /token=live-token/);
    assert.equal(element('connection').textContent, '실시간');
    assert.equal(element('demo-banner').hidden, true);
    assert.equal(data.get('labhq_token'), 'live-token');
  } finally {global.setInterval = originalInterval;}
});
