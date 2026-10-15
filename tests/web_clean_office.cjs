const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

require('../labhq/web/state.js');
const state = globalThis.LabHQState;

const reviewed = {id:'past', text:'Reviewed result', status:'done', pi_reviewed_at:90,
  created_at:10, updated_at:90, cost_usd:30};
const pending = {id:'review', text:'Needs review', status:'failed', pi_review_pending:true,
  created_at:20, updated_at:80, cost_usd:3};
const running = {id:'run', text:'Running now', status:'running', created_at:30, updated_at:70, cost_usd:4};

test('reviewed terminal requests leave the office while review-pending requests stay', () => {
  assert.equal(state.isPastRequest(reviewed), true);
  assert.equal(state.isPastRequest(pending), false);
  assert.deepEqual(state.officeRequests([reviewed, pending, running]).map(q => q.id), ['review', 'run']);

  const office = state.createOfficeState();
  office.apply({type:'snapshot', data:{session_started_at:100, requests:[reviewed, pending, running]}});
  assert.equal(office.S.current, 'review', 'review waiting remains the first office request');

  office.apply({type:'snapshot', data:{session_started_at:100, requests:[reviewed]}});
  assert.equal(office.S.current, null, 'past work never restores onto the opening board');
});

test('header cost keeps current-session and office requests, with unknown costs visible', () => {
  const recentPast = {...reviewed, id:'recent', created_at:110, cost_usd:2};
  const unknown = {...pending, id:'unknown', cost_usd:0, cost_known:false};
  const shown = state.sessionCostRequests([reviewed, recentPast, unknown, running], 100);
  assert.deepEqual(shown.map(q => q.id), ['recent', 'unknown', 'run']);
  assert.equal(state.requestsCostTotal(shown), 6);
  assert.equal(state.totalCostLabel(shown, state.requestsCostTotal(shown)),
    '사용 비용 $6.00 + 미집계된 비용');
});

test('2.5D and 3D keep past requests collapsed and show a clean empty board', () => {
  const root = path.resolve(__dirname, '../labhq/web');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const live3d = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');
  assert.match(html, /지난 요청/);
  assert.match(html, /새 요청을 기다리는 중/);
  assert.match(live3d, /지난 요청/);

  const office = state.createOfficeState();
  office.apply({type:'snapshot', data:{requests:[reviewed]}});
  const board = {dataset:{}, innerHTML:''};
  const context = vm.createContext({S:office.S, LabHQState:state, $:() => board,
    snapshotLoaded:true, esc:String, processingLabel:state.processingLabel,
    phaseList:()=>''});
  const render = html.slice(html.indexOf('function renderBoard()'), html.indexOf('function renderRack()'));
  vm.runInContext(render + '\nrenderBoard();', context);
  assert.match(board.innerHTML, /새 요청을 기다리는 중/);
  assert.doesNotMatch(board.innerHTML, /Reviewed result/);
});
