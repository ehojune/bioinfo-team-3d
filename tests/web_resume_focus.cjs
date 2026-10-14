const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
require('../labhq/web/state.js');
const state = globalThis.LabHQState;
const snapshot = (office, requests, recent_events = []) => office.apply({type:'snapshot', data:{requests, recent_events}});
const old = {id:'old', text:'Continue research', status:'running', created_at:10, updated_at:90};
const done = {id:'new', text:'Save output', status:'done', created_at:80, updated_at:80};
const storage = () => {const data = new Map(); return {getItem:k => data.get(k), setItem:(k,v) => data.set(k,v)};};

test('snapshot chooses unfinished work by last activity, not newer completed or idle work', () => {
  const office = state.createOfficeState();
  snapshot(office, [old, done]);
  assert.equal(office.S.current, 'old');
  snapshot(office, [old, done, {id:'idle', status:'waiting_pi', created_at:60, updated_at:60}]);
  assert.equal(office.S.current, 'old');
  snapshot(office, [{...old, status:'interrupted'}, done]);
  assert.equal(office.S.current, 'old');
  snapshot(office, [{...old, status:'done'}, done]);
  assert.equal(office.S.current, 'old');
});

test('last viewed request survives reload and replay with demo and instance isolation', () => {
  const local = storage();
  const create = boot => state.createOfficeState({focusStorage:state.createRequestFocusStorage(boot, () => local)});
  const office = create({instance:'one'});
  snapshot(office, [old, done]);
  office.selectRequest('new');
  const reload = create({instance:'one'});
  snapshot(reload, [old, done], [{type:'request.created', request_id:'old', ts:10, data:{text:old.text}}]);
  assert.equal(reload.S.current, 'new');
  for (const boot of [{instance:'two'}, {instance:'one', demo:true}]) {
    const separate = create(boot); snapshot(separate, [old, done]);
    assert.equal(separate.S.current, 'old');
  }
  snapshot(reload, [old]);
  assert.equal(reload.S.current, 'old', 'removed preference falls back');
  const unavailable = state.createOfficeState({focusStorage:state.createRequestFocusStorage({}, () => {throw Error('denied');})});
  snapshot(unavailable, [old, done]); unavailable.selectRequest('new');
  assert.equal(unavailable.S.current, 'new');
});

test('request ordering groups unfinished before terminal and uses event activity', () => {
  const office = state.createOfficeState();
  snapshot(office, [old, done, {id:'waiting', status:'interrupted', created_at:1, updated_at:100},
    {id:'failed', status:'failed', created_at:110}]);
  assert.deepEqual(state.sortRequests(office.S.requests.values()).map(q => q.id), ['waiting','old','failed','new']);
  office.apply({type:'agent.log', request_id:'old', ts:120, data:{text:'Progress'}});
  assert.deepEqual(state.sortRequests(office.S.requests.values()).map(q => q.id), ['old','waiting','failed','new']);
});

test('task selector redraws in activity order and labels terminal states', () => {
  const office = state.createOfficeState();
  snapshot(office, [old, done, {id:'waiting', text:'Await decision', status:'waiting_pi', created_at:1}]);
  const html = fs.readFileSync(path.join(__dirname, '../labhq/web/index.html'), 'utf8');
  const render = html.slice(html.indexOf('function renderRequest()'), html.indexOf('  const proj = S.projects.find'));
  const ctx = vm.createContext({S:office.S, LabHQState:state, $:() => ({}), reqKey:'', snapshotLoaded:true,
    esc:String, short:String, nick:String});
  vm.runInContext(render + '\nglobalThis.pick = pick; }\nrenderRequest();', ctx);
  assert.match(ctx.pick, /request-title">Save output<\/span>.*완료<\/span>/);
  assert.match(ctx.pick, /data-request-id="old"/);
  assert.doesNotMatch(ctx.pick, /<select/);
  assert.ok(ctx.pick.indexOf('value="old"') < ctx.pick.indexOf('value="waiting"'));
  office.apply({type:'agent.log', request_id:'waiting', ts:150, data:{text:'Question'}});
  vm.runInContext('renderRequest();', ctx);
  assert.ok(ctx.pick.indexOf('value="waiting"') < ctx.pick.indexOf('value="old"'));
  assert.ok(ctx.pick.indexOf('value="old"') < ctx.pick.indexOf('value="new"'));
});

test('manual selection survives completed arrivals and reconnect; new active request is followed', () => {
  const office = state.createOfficeState(); snapshot(office, [old, done]);
  office.selectRequest('old');
  office.apply({type:'request.created', request_id:'finished', ts:100, data:{status:'done', text:'Finished'}});
  assert.equal(office.S.current, 'old');
  assert.equal(office.S.requests.get('finished').status, 'done');
  snapshot(office, [old, done], [{type:'request.created', request_id:'new', ts:80, data:{text:done.text}}]);
  assert.equal(office.S.current, 'old');
  office.apply({type:'request.created', request_id:'active', ts:110, data:{text:'New active request'}});
  assert.equal(office.S.current, 'active');
});

test('whiteboard shows continuation from resume event and snapshot, then removes it on completion', () => {
  const office = state.createOfficeState();
  office.apply({type:'request.created', request_id:'old', ts:10, data:{text:old.text}});
  assert.equal(state.requestResumeLabel(office.S.requests.get('old')), '');
  office.apply({type:'request.resumed', request_id:'old', ts:20});
  assert.equal(state.requestResumeLabel(office.S.requests.get('old')), '이어서 진행 중');
  const html = fs.readFileSync(path.join(__dirname, '../labhq/web/index.html'), 'utf8');
  const render = html.slice(html.indexOf('function renderBoard()'), html.indexOf('function renderRack()'));
  const board = {innerHTML:'', dataset:{}};
  const ctx = vm.createContext({S:office.S, LabHQState:state, $:() => board, boardKey:'', snapshotLoaded:true,
    esc:String, short:String, processingLabel:state.processingLabel, phaseList:() => '', nick:String});
  vm.runInContext(render + '\nrenderBoard();', ctx);
  assert.match(board.innerHTML, /이어서 진행 중/);
  office.apply({type:'request.completed', request_id:'old', data:{ok:true}});
  vm.runInContext('renderBoard();', ctx);
  assert.doesNotMatch(board.innerHTML, /이어서 진행 중/);
  snapshot(office, [old]);
  assert.equal(state.requestResumeLabel(office.S.requests.get('old')), '이어서 진행 중');
});
