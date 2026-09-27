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
  const json = JSON.stringify(state, (_, value) => Object.prototype.toString.call(value)==='[object Map]' ? [...value] : value);
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
console.log(`${events.length} legacy event states, replay reset, effects, isolation and bounds: OK`);
