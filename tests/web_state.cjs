const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const create = context.LabHQState.createOfficeState;
const office = create({now: () => 1000});
const agent = {id:'analyst', name:'Analyst', employment:'core'};
const plan = {steps:[{id:'s1', agent_id:'analyst', instruction:'Inspect'}, {id:'s2', depends_on:['s1']}]};
const events = [
  ['snapshot', {agents:[agent], requests:[], approvals:[], projects:[{id:'demo'}]}],
  ['roster.updated', {agents:[agent, {id:'recruiter'}, {id:'cso'}, {id:'c_one', employment:'contract'}]}],
  ['runner.online', {runner_id:'runner_demo'}], ['runner.offline', {runner_id:'runner_demo'}],
  ['request.created', {text:'Public example', mode:'orchestrate'}], ['request.plan', plan],
  ['task.dispatched', {step_id:'s1', kind:'step', prompt:'Your step (s1): Inspect\n\nInputs', title:'Inspect'}],
  ['agent.status', {state:'working', task:'Inspect'}],
  ['agent.tool', {name:'Read', input:'public.txt'}], ['agent.tool_error', {text:'Example error'}],
  ['agent.log', {text:'Progress'}], ['agent.log', {level:'thinking', text:'Not displayed'}],
  ['agent.usage', {cost_usd:0.25}], ['agent.usage', {cost_usd:-1}],
  ['approval.requested', {id:'a1', summary:'Example approval', kind:'tool_permission'}],
  ['agent.status', {state:'waiting'}], ['approval.resolved', {id:'a1', approved:false, note:'Example'}],
  ['job.submitted', {job_id:'42', name:'example'}], ['agent.status', {state:'hibernating'}],
  ['job.state', {job_id:'42', state:'completed', exit_status:0}], ['jobs.finished', {}],
  ['task.result', {ok:false}], ['request.step_done', {step_id:'s1', ok:true}],
  ['request.step_skipped', {step_id:'s2'}], ['request.questions', {questions:['Which output?']}],
  ['request.review', {verdict:'revise', issues:[{step_id:'s1', problem:'Missing check'}]}],
  ['request.review', {status:'review_unparsed'}],
  ['request.review', {verdict:'accept', scores:{addresses_question:5, evidence:4, thoroughness:4}}],
  ['recruit.suggested', {repo:'example/tool'}], ['recruit.suggested', {repo:'example/tool'}],
  ['recruit.status', {slug:'tool', stage:'converting'}],
  ['recruit.done', {agent:{id:'c_tool', name:'Tool', employment:'contract'}, passed_probation:true}],
  ['recruit.failed', {error:'Example failure'}], ['github.posted', {kind:'plan', url:'https://example.org/report'}],
  ['github.failed', {error:'Example failure'}], ['agent.status', {state:'error', error:'Example failure'}],
  ['agent.status', {state:'done'}], ['request.completed', {ok:true, report:'Complete'}],
  ['request.failed', {error:'Example failure'}],
  ['roster.updated', {agents:[agent]}],
  ['snapshot', {agents:[agent], requests:[{id:'r1', text:'Restored', status:'interrupted', cost_usd:2,
    plan, step_status:{s1:'done',s2:'skipped'}}], approvals:[], recent_events:[
      {type:'agent.usage', request_id:'r1', data:{cost_usd:0.25}},
      {type:'agent.status', agent_id:'analyst', data:{state:'done'}},
    ]}],
  ['snapshot', {}], ['unknown.event', {}],
].map(([type,data], i) => ({type, data, agent_id:'analyst', request_id:'r1', task_id:'t1', ts:1000+i, seq:i+1,
  ...(i===40?{replay_gap:{requested_since:1,oldest_seq:40}}:{})}));
function digest(state) {
  const json = JSON.stringify(state, (key, value) => key === 'costKnown' ? undefined
    : Object.prototype.toString.call(value)==='[object Map]' ? [...value] : value);
  return createHash('sha256').update(json).digest('hex');
}
// Golden hashes come from b953d13's original index.html reducer, with time fixed.
const golden = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/web_state_hashes.json'), 'utf8'));
const actual = [];
for (const ev of events) { office.apply(ev); actual.push(digest(office.S)); }
assert.deepEqual(actual, golden, 'legacy state must match after every event, including replay-gap snapshots');
assert.equal(office.S.agents.size, 0);
assert.equal(office.S.cost, 0);
assert.equal(create().S.agents.size, 0, 'instances must not share state');
const fresh = create({now:()=>1000});
fresh.apply({type:'roster.updated',data:{agents:[agent]}});
assert.deepEqual(JSON.parse(JSON.stringify(fresh.apply({type:'agent.status',agent_id:'analyst',data:{state:'done'}}))),
  [{type:'renderAfter',ms:3200}]);
assert.equal(fresh.apply({type:'agent.status',agent_id:'analyst',data:{state:'done'}}, true).length, 0);
assert.equal(fresh.apply({type:'approval.requested',data:{id:'a2',summary:'Please review'}}, true).length, 0);
for(let i=0;i<150;i++)fresh.apply({type:'agent.log',agent_id:'analyst',ts:1000+i*10,data:{text:'Progress'}});
assert.equal(fresh.S.feed.length,120); assert.equal(fresh.S.agents.get('analyst').log.length,40);
fresh.apply({type:'agent.usage',request_id:'r2',data:{tokens:{input_tokens:9},cost_known:false}});
assert.equal(fresh.S.requests.get('r2').costKnown,false);
fresh.apply({type:'request.note',request_id:'r2',ts:1234,data:{id:'note_1',text:'표도 그려 주세요',at:1234}});
assert.equal(fresh.S.requests.get('r2').piNotes[0].text,'표도 그려 주세요');
const notesSnapshot=create();
notesSnapshot.apply({type:'snapshot',data:{requests:[{id:'r2',status:'running',pi_notes:[{id:'note_1',text:'표도 그려 주세요',at:1234}]}]}});
assert.equal(notesSnapshot.S.requests.get('r2').piNotes[0].at,1234,'a late client restores PI notes');
fresh.apply({type:'pipeline.pr',request_id:'r2',data:{status:'pending',name:'tiny',reason:'write permission required'}});
assert.equal(fresh.S.requests.get('r2').pipelinePr.status,'pending');
fresh.apply({type:'pipeline.pr',request_id:'r2',data:{status:'open',name:'tiny',url:'https://example.org/pr/9',number:9}});
assert.equal(fresh.S.requests.get('r2').github.at(-1).kind,'pipeline_pr');
{ // #301 review: a late client restores the stored status from the snapshot, without a duplicate link
  const late = create();
  late.apply({type:'snapshot',data:{requests:[{id:'p',status:'done',pipeline_pr:{status:'pending',name:'tiny',reason:'write permission required'}}]}});
  assert.equal(late.S.requests.get('p').pipelinePr.reason,'write permission required');
  const open = {status:'open',name:'tiny',url:'https://example.org/pr/9',number:9};
  late.apply({type:'snapshot',data:{recent_events:[{type:'pipeline.pr',request_id:'p',data:open}],
    requests:[{id:'p',status:'done',pipeline_pr:open}]}});
  assert.equal(late.S.requests.get('p').github.filter(g => g.kind === 'pipeline_pr').length, 1);
}
fresh.apply({type:'request.plan',request_id:'r3',data:{steps:[
  {id:'s1',agent_id:'analyst',instruction:'Inspect',outputs:['report.txt'],depends_on:[]},
  {id:'s2',agent_id:'analyst',instruction:'Review report',depends_on:['s1']},
]}});
fresh.apply({type:'request.step_attempt',request_id:'r3',data:{step_id:'s1',attempt:2}});
fresh.apply({type:'task.dispatched',request_id:'r3',task_id:'task-3',agent_id:'analyst',data:{step_id:'s1',kind:'step'}});
fresh.apply({type:'task.result',request_id:'r3',task_id:'task-3',agent_id:'analyst',data:{
  ok:false,text:'partial',outputs:['report.txt'],missing_outputs:['table.tsv'],error:'missing table',
}});
fresh.apply({type:'request.review',request_id:'r3',data:{verdict:'revise',issues:[
  {step_id:'s1',problem:'표가 없습니다',request:'표를 추가하세요'},
]}});
const stepDetail=fresh.S.stepDetails.get('r3:s1');
assert.equal(stepDetail.attempts,2);assert.deepEqual(Array.from(stepDetail.outputs),['report.txt']);
assert.deepEqual(Array.from(stepDetail.missing_outputs),['table.tsv']);
assert.equal(stepDetail.review_issues[0].problem,'표가 없습니다');
fresh.apply({type:'request.step_quota_wait',request_id:'r3',data:{step_id:'s2',engine:'codex',resume_at:2000}});
assert.equal(fresh.req('r3').steps.s2,'waiting_quota');
assert.equal(fresh.S.stepDetails.get('r3:s2').quota_resume_at,2000);
fresh.apply({type:'request.step_quota_resumed',request_id:'r3',data:{step_id:'s2',engine:'codex',manual:true}});
assert.equal(fresh.req('r3').steps.s2,'pending');
assert.equal(fresh.S.stepDetails.get('r3:s2').quota_resume_at,undefined);
// #302: the request leaves 한도 대기 when its last quota step resumes, not before.
const quota=create();
quota.apply({type:'snapshot',data:{requests:[{id:'q',status:'waiting_quota',created_at:1,
  step_status:{a:'waiting_quota',b:'waiting_quota',c:'done'}}]}});
quota.apply({type:'request.step_quota_resumed',request_id:'q',data:{step_id:'a',engine:'codex',manual:false}});
assert.equal(quota.req('q').status,'waiting_quota','another step still waits for quota');
quota.apply({type:'request.step_quota_resumed',request_id:'q',data:{step_id:'b',engine:'claude_code',manual:false}});
assert.equal(quota.req('q').status,'running','the last quota step resumed');
assert.equal(quota.S.current,'q');
quota.apply({type:'request.step_quota_wait',request_id:'q',data:{step_id:'a',engine:'codex',resume_at:3000}});
assert.equal(quota.req('q').status,'waiting_quota','a new wait shows again');
quota.apply({type:'request.step_done',request_id:'q',data:{step_id:'a',ok:false,reason:'quota_wait_limit'}});
assert.equal(quota.req('q').status,'running','a quota step that ended leaves no wait');
quota.apply({type:'request.failed',request_id:'q',data:{error:'quota_wait_limit'}});
quota.apply({type:'request.step_quota_wait',request_id:'q',data:{step_id:'b',engine:'codex',resume_at:3000}});
assert.equal(quota.req('q').status,'failed','a late wait event does not reopen a finished request');
const activeSnapshot=create();
activeSnapshot.apply({type:'snapshot',data:{requests:[
  {id:'done-newer',status:'done',created_at:2},
  {id:'quota-active',status:'waiting_quota',created_at:1},
]}});
assert.equal(activeSnapshot.S.current,'quota-active','quota wait remains the selected active request');
assert.notEqual(activeSnapshot.req('quota-active').phase,'done','quota wait is not rendered as terminal');
for (const terminal of ['request.completed', 'request.failed']) {
  const costs = create();
  costs.apply({type:'agent.usage',request_id:'other',data:{cost_usd:2}});
  costs.apply({type:'agent.usage',request_id:'r',data:{cost_usd:0.25}});
  costs.apply({type:terminal,request_id:'r',data:{cost_usd:1.5,cost_known:true}});
  assert.equal(costs.req('r').cost,1.5, 'terminal total replaces live subtotal');
  assert.equal(costs.S.cost,3.5, 'other requests keep their costs');
  costs.apply({type:terminal,request_id:'r',data:{cost_usd:1.5,cost_known:false,cost_summary:{
    actual_usd:1,estimated_usd:0.5,unknown_count:1,
    by_engine:{claude_code:{actual_usd:1,estimated_usd:0,unknown_count:0},codex:{actual_usd:0,estimated_usd:0.5,unknown_count:1}},warnings:[],
  }}});
  assert.equal(costs.req('r').costSummary.unknown_count,1);
  assert.equal(costs.req('r').costSummary.by_engine.codex.estimated_usd,0.5);
  costs.apply({type:terminal,request_id:'r',data:{cost_usd:1.5}});
  assert.equal(costs.S.cost,3.5, 'repeated terminal total is idempotent');
  costs.apply({type:terminal,request_id:'r',data:{cost_known:false}});
  assert.equal(costs.req('r').cost,1.5, 'missing final cost preserves known subtotal');
  assert.equal(costs.req('r').costKnown,false);
  costs.apply({type:terminal,request_id:'r',data:{cost_usd:0}});
  assert.equal(costs.S.cost,2, 'zero final cost corrects the subtotal');
  for (const cost_usd of [null, -1, 'bad', Infinity]) {
    costs.apply({type:terminal,request_id:'r',data:{cost_usd}});
    assert.equal(costs.S.cost,2, 'invalid final cost is ignored');
  }
  const finalOnly = create();
  finalOnly.apply({type:terminal,request_id:'r',data:{cost_usd:0.3}});
  assert.equal(finalOnly.req('r').cost,0.3, 'result-only cost is visible immediately');
  assert.equal(finalOnly.S.cost,0.3);
  finalOnly.apply({type:'snapshot',data:{requests:[{id:'r',status:'done',cost_usd:0.3}],
    recent_events:[{type:terminal,request_id:'r',data:{cost_usd:0.3}}]}});
  assert.equal(finalOnly.S.cost,0.3, 'snapshot replaces replayed totals');
}
const failedCosts = create();
failedCosts.apply({type:'agent.usage',request_id:'failed',data:{cost_usd:0.2}});
failedCosts.apply({type:'agent.usage',request_id:'failed',data:{cost_usd:0.3,cost_known:false}});
failedCosts.apply({type:'request.failed',request_id:'failed',data:{
  error:'exception',cost_usd:1.25,cost_known:false,
}});
assert.equal(failedCosts.req('failed').cost,1.25, 'exception terminal uses the persisted final total');
assert.equal(failedCosts.S.cost,1.25);
assert.equal(failedCosts.req('failed').costKnown,false);
// #270: an unaccounted task is counted apart and never shown as $0; estimates are labelled as such.
{
  const {costLabel, engineCostLabel, totalCostLabel} = context.LabHQState;
  const mixed = {actual_usd:1, estimated_usd:0.5, subtotal_usd:1.5, unknown_count:1, by_engine:{
    claude_code:{actual_usd:1, estimated_usd:0, unknown_count:0}, codex:{actual_usd:0, estimated_usd:0.5, unknown_count:1}},
    prices:[{engine:'codex', model:'gpt-6.1-sol', checked_on:'2026-10-02', stale:true}], warnings:['price_stale:codex:gpt-6.1-sol']};
  const onlyUnknown = {actual_usd:0, estimated_usd:0, subtotal_usd:0, unknown_count:2,
    by_engine:{codex:{actual_usd:0, estimated_usd:0, unknown_count:2}}, prices:[], warnings:[]};
  const estimated = {actual_usd:0, estimated_usd:0.2, subtotal_usd:0.2, unknown_count:0,
    by_engine:{codex:{actual_usd:0, estimated_usd:0.2, unknown_count:0}}, prices:[], warnings:[]};
  const view = create();
  view.apply({type:'snapshot', data:{requests:[
    {id:'mixed', status:'done', cost_usd:1.5, cost_known:false, cost_summary:mixed},
    {id:'unknown', status:'done', cost_usd:0, cost_known:false, cost_summary:onlyUnknown},
    {id:'estimated', status:'done', cost_usd:0.2, cost_known:true, cost_summary:estimated},
    {id:'legacy', status:'done', cost_usd:0.4, cost_known:false},
  ]}});
  assert.equal(costLabel(view.req('mixed')), '확인 $1.00 + 추정 $0.50 + 미집계 1건');
  assert.equal(costLabel(view.req('unknown')), '미집계 2건', 'unknown-only is not $0');
  assert.equal(costLabel(view.req('legacy')), '$0.40 + 비용 미집계');
  assert.equal(view.req('unknown').costKnown, false);
  assert.equal(view.req('estimated').costKnown, true);
  const engines = engineCostLabel(view.req('mixed').costSummary);
  assert.match(engines, /^claude_code 확인 \$1\.00 · codex 추정 \$0\.50 \+ 미집계 1건; 추정은 API 가격표 환산/);
  assert.match(engines, /가격표 오래됨: codex gpt-6\.1-sol \(2026-10-02 확인\)/);
  assert.equal(totalCostLabel([...view.S.requests.values()], view.S.cost), '사용 비용 $2.10 (추정 $0.70 포함) + 미집계 3건 이상');
  view.apply({type:'request.followup_done', request_id:'unknown', data:{id:'f', ok:true, answer:'a', cost_usd:0.1,
    cost_known:true, cost_summary:{...estimated, estimated_usd:0.1, subtotal_usd:0.1}}});
  assert.equal(costLabel(view.req('unknown')), '추정 $0.10', 'a follow-up summary replaces the request summary');
  assert.ok(!Object.keys(view.req('mixed')).includes('costSummary'), 'state snapshots stay comparable');
  // A live cost the stored summary does not hold yet replaces the stale summary with the running total.
  view.apply({type:'agent.usage', request_id:'estimated', data:{tokens:{input_tokens:5}, cost_known:false}});
  assert.equal(costLabel(view.req('estimated')), '$0.20 + 비용 미집계', 'running Codex task is not shown as settled');
  view.apply({type:'agent.usage', request_id:'mixed', data:{cost_usd:0.25}});
  assert.equal(costLabel(view.req('mixed')), '$1.75 + 비용 미집계', 'live amount is not hidden behind the old summary');
}
console.log(`${events.length} legacy event states, replay reset, effects, isolation and bounds: OK`);
