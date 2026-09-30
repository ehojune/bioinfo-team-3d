const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
require(path.join(root,'labhq/web/state.js'));

const office=global.LabHQState.createOfficeState({now:()=>1000});
const recent_events=Array.from({length:200},(_,i)=>({type:'agent.log',seq:i+1,agent_id:'analyst',data:{level:'debug',text:'filler'}}));
office.apply({type:'snapshot',data:{
  agents:[{id:'analyst'}],recent_events,
  requests:[{id:'r1',status:'running',plan:{steps:[{id:'s1',agent_id:'analyst'}]},step_status:{}}],
  running_tasks:[{id:'task-1',request_id:'r1',step_id:'s1',agent_id:'analyst',state:'running'}],
}});
assert.equal(office.S.requests.get('r1').steps.s1,'working','200개 밖 task도 실행 중으로 복원한다');
assert.equal(office.S.stepDetails.get('r1:s1').task_id,'task-1','복원한 task는 취소 ID를 보존한다');
const server=fs.readFileSync(path.join(root,'labhq/gateway/server.py'),'utf8');
assert.match(server,/"running_tasks"\s*:\s*self\.running_tasks\(\)/,'gateway snapshot이 활성 task를 보낸다');
console.log('snapshot restores an active step after a 200-event replay gap: OK');
