const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../labhq/web/state.js'), 'utf8'), context);
function office() {
  const o = context.LabHQState.createOfficeState({now: () => 640});
  o.apply({type: 'snapshot', data: {agents: [{id: 'analyst', runner_id: 'r'}], runners: ['r']}});
  const event = (type, data = {}, task_id = 't', ts = 100) => o.apply({type, agent_id: 'analyst', task_id, ts, data});
  event('task.dispatched', {title: 'Inspect public matrix'});
  return {o, event, a: o.ag('analyst')};
}
test('long command stays busy despite stale done from another task', () => {
  const {o, event, a} = office();
  event('agent.status', {state: 'working'});
  event('agent.tool', {id: 'bash1', name: 'Bash', input: {command: 'python analyze.py\n# second line'}});
  event('agent.status', {state: 'done'}, 'older');
  assert.equal(o.visual(a), 'working');
  assert.equal(o.stateText(a), '명령 실행 중 · 9분째');
  assert.equal(a.log.at(-1).text, '도구 명령 실행 python analyze.py # second line');
  assert.match(a.log.at(-1).fullText, /"command"/);
  event('agent.tool_result', {id: 'bash1'});
  assert.equal(o.stateText(a), '생각하는 중');
  event('task.result', {ok: true});
  assert.equal(o.visual(a), 'idle');
});
test('a public lookup waiting on its service shows elapsed minutes over the limit (#676)', () => {
  const {o, event, a} = office();
  event('agent.status', {state: 'working'});
  event('agent.tool', {id: 'mcp1', name: 'mcp__labhq_annot__fulltext', input: {}});
  const lookup = (status, elapsed_s) => ({level: status === 'timeout' ? 'warn' : 'info', text: 'fulltext',
    external_lookup: {tool: 'fulltext', status, elapsed_s, timeout_s: 1200}});
  event('agent.log', lookup('waiting', 120), 't', 640);
  assert.equal(o.stateText(a), '외부 응답 대기 2분째 / 상한 20분');
  event('agent.log', lookup('waiting', 5000), 't', 640);
  assert.equal(o.stateText(a), '외부 응답 대기 20분째 / 상한 20분');
  event('agent.log', lookup('ok', 300), 't', 640);
  assert.equal(o.stateText(a), '도구 실행 중 · 9분째');
});
for (const [data, label] of [
  [{state: 'queued'}, '차례 기다리는 중'],
  [{state: 'waiting', approval: 'p'}, 'PI 결정 기다리는 중'],
  [{state: 'waiting', ask: 'q'}, '답 기다리는 중'],
  [{state: 'hibernating', jobs: ['j']}, 'HPC 작업 기다리는 중'],
  [{state: 'hibernating', asks: ['q']}, '답 기다리는 중'],
]) test(label, () => {
  const {o, event, a} = office();
  if (data.approval) o.S.approvals.set(data.approval, {id:data.approval, agent_id:a.id});
  event('agent.status', data);
  assert.notEqual(o.visual(a), 'idle');
  assert.equal(o.stateText(a), label);
});
test('reconnect restores task and tool even outside recent event window', () => {
  const {o} = office();
  o.apply({type: 'snapshot', data: {agents: [{id: 'analyst', runner_id: 'r'}], runners: ['r'],
    running_tasks: [{id: 't', agent_id: 'analyst', state: 'running', title: 'Inspect matrix',
      activity: {state: 'working', tools: {bash1: {name: 'Bash', input: '{"command":"sleep 900"}', at: 100}}}}]}});
  const a = o.ag('analyst');
  assert.equal(o.visual(a), 'working');
  assert.equal(o.stateText(a), '명령 실행 중 · 9분째');
  assert.equal(o.currentTask(a), 'Inspect matrix');
});
test('snapshot restores question and approval waits', () => {
  const {o} = office();
  for (const [activity, label] of [[{state: 'waiting', ask: 'q'}, '답 기다리는 중'],
    [{state: 'waiting', approval: 'p'}, 'PI 결정 기다리는 중'],
    [{state: 'hibernating', jobs: ['j']}, 'HPC 작업 기다리는 중']]) {
    o.apply({type: 'snapshot', data: {agents: [{id: 'analyst'}], approvals: activity.approval ? [{id:activity.approval,agent_id:'analyst'}] : [], running_tasks: [{id: 't', agent_id: 'analyst', state: 'running', activity}]}});
    assert.equal(o.stateText(o.ag('analyst')), label);
  }
});
test('stall and runner disconnect override busy display', () => {
  const {o, event, a} = office();
  event('agent.tool', {id: 'bash1', name: 'Bash', input: {command: 'sleep 900'}});
  event('agent.log', {level: 'warn', text: '출력이 10분째 없습니다'});
  assert.equal(o.visual(a), 'error');
  assert.equal(o.stateText(a), '출력이 10분째 없습니다');
  event('agent.tool_result', {id: 'bash1'});
  assert.equal(o.visual(a), 'working');
  event('runner.offline', {runner_id: 'r'});
  assert.equal(o.visual(a), 'error');
  assert.equal(o.stateText(a), 'runner 연결 끊김');
});
test('Write input uses path with raw input available to expand', () => {
  const {event, a} = office();
  event('agent.tool', {id: 'w', name: 'Write', input: {file_path: 'outputs/report.md', content: 'long contents'}});
  assert.match(a.log.at(-1).text, /outputs\/report.md$/);
  assert.doesNotMatch(a.log.at(-1).text, /content|file_path/);
  assert.match(a.log.at(-1).fullText, /long contents/);
});
test('matching tool completion keeps other concurrent tools active', () => {
  const {o, event, a} = office();
  event('agent.tool', {id: 'a', name: 'Bash', input: 'first'});
  event('agent.tool', {id: 'b', name: 'Read', input: 'second'});
  event('agent.tool_result', {id: 'b'});
  assert.equal(o.stateText(a), '명령 실행 중 · 9분째');
  event('agent.tool_result', {id: 'a'});
  assert.equal(o.stateText(a), '생각하는 중');
});

test('a queued task cannot hide an existing command or its warning', () => {
  const {o, event, a} = office();
  event('agent.tool', {id: 'a', name: 'Bash', input: 'sleep 900'});
  event('task.dispatched', {title: 'Queued consultation'}, 'next', 200);
  event('agent.status', {state: 'queued'}, 'next', 201);
  assert.equal(o.stateText(a), '명령 실행 중 · 9분째');
  event('agent.log', {level: 'warn', text: '출력이 없습니다'});
  assert.equal(o.stateText(a), '출력이 없습니다');
});

test('snapshot with only a running task restores busy without status events', () => {
  const {o} = office();
  o.apply({type: 'snapshot', data: {agents: [{id: 'analyst'}],
    running_tasks: [{id: 't', agent_id: 'analyst', state: 'running', step_id: 'analysis'}], recent_events: []}});
  const a = o.ag('analyst');
  assert.equal(o.visual(a), 'working');
  assert.equal(o.stateText(a), '생각하는 중');
  assert.equal(o.currentTask(a), 'analysis');
});

test('staff tasks restore followup activity independently of the request board', () => {
  const {o} = office();
  o.apply({type: 'snapshot', data: {agents: [{id: 'analyst'}], running_tasks: [],
    staff_tasks: [{id: 'followup', agent_id: 'analyst', state: 'running', title: 'Followup',
      activity: {state: 'working', tools: {b: {name: 'Bash', input: 'sleep 900', at: 100}}}}]}});
  assert.equal(o.stateText(o.ag('analyst')), '명령 실행 중 · 9분째');
  assert.equal(o.currentTask(o.ag('analyst')), 'Followup');
});

test('staff controls override busy text while warnings still take priority', () => {
  const {o, event, a} = office();
  event('agent.tool', {id: 'b', name: 'Bash', input: 'sleep 900'});
  event('agent.control', {paused: true});
  assert.equal(o.stateText(a), '일시 정지됨');
  assert.equal(o.visual(a), 'waiting');
  assert.equal(o.currentTool(a), null);
  event('agent.log', {level: 'warn', text: '출력이 없습니다'});
  assert.equal(o.stateText(a), '출력이 없습니다');
  event('agent.control', {paused: false});
  event('agent.tool_result', {id: 'b'});
  assert.equal(o.stateText(a), '생각하는 중');
  event('task.result', {ok: false, error_kind: 'halted'});
  assert.equal(o.stateText(a), '멈춤 · 재개 가능');
  event('request.step_halt_expired');
  assert.equal(o.S.activeTasks.size, 0);
});

test('snapshot restores paused and halted tasks without claiming they are thinking', () => {
  const {o} = office();
  for (const [state, label] of [['paused', '일시 정지됨'], ['halted', '멈춤 · 재개 가능']]) {
    o.apply({type: 'snapshot', data: {agents: [{id: 'analyst'}],
      running_tasks: [{id: 't', agent_id: 'analyst', state, control: {[state]: true}}]}});
    assert.equal(o.stateText(o.ag('analyst')), label);
  }
});

test('a new turn removes the prior hibernating task of the same step', () => {
  const {o} = office();
  const send = (type, task_id, data) => o.apply({type, task_id, request_id: 'r', agent_id: 'analyst', data});
  send('task.dispatched', 'old', {step_id: 's'});
  send('task.result', 'old', {pending_jobs: ['j']});
  send('task.dispatched', 'new', {step_id: 's'});
  assert.equal(o.S.activeTasks.has('old'), false);
});
test('a completed request and authoritative snapshot remove stale busy tasks', () => {
  const {o, event, a} = office();
  o.S.activeTasks.get('t').request_id = 'req';
  o.apply({type: 'request.completed', request_id: 'req', data: {ok: true}});
  assert.equal(o.S.activeTasks.size, 0);
  event('task.dispatched');
  o.apply({type: 'snapshot', data: {agents: [{id: 'analyst'}], running_tasks: [],
    recent_events: [{type: 'task.dispatched', task_id: 'old', agent_id: 'analyst', data: {}}]}});
  assert.equal(o.S.activeTasks.size, 0);
  assert.equal(o.visual(o.ag('analyst')), 'idle');
});
