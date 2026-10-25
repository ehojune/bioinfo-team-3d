const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
require('../labhq/web/state.js');
const web = path.resolve(__dirname, '../labhq/web');
function fixture(step_status = {}) {
  const o = LabHQState.createOfficeState({now: () => 100});
  o.apply({type:'snapshot', data:{agents:[{id:'analyst'}, {id:'cso'}, {id:'engineer'}], requests:[
    {id:'old', text:'Previous study', status:'done', pi_review_pending:true,
      plan:{steps:[{id:'old-step', agent_id:'analyst'}]}},
    {id:'current', text:'Current study', status:'running',
      plan:{steps:[{id:'analysis', agent_id:'analyst'}]}, step_status},
  ]}});
  return o;
}
test('a current planned turn wins over an older review even when the old request is selected', () => {
  const o = fixture(); o.selectRequest('old');
  assert.equal(o.visual(o.ag('analyst')), 'queued');
  assert.equal(o.stateText(o.ag('analyst')), '차례 대기');
  assert.equal(o.visual(o.ag('cso')), 'idle');
  assert.equal(o.visual(o.ag('engineer')), 'idle');
});
test('current step status survives snapshots without a dispatched task', () => {
  for (const [step, visual, text] of [['working','working','작업 중'],
    ['done','done','담당 단계 완료'], ['waiting_login','waiting','로그인 대기'],
    ['halted','waiting','멈춤 · 재개 가능']]) {
    const o = fixture({analysis:step}), a = o.ag('analyst');
    assert.equal(o.visual(a), visual, step);
    assert.equal(o.stateText(a), text, step);
  }
});
test('live command, approval and warning still override request fallback', () => {
  const o = fixture(), a = o.ag('analyst');
  const event = (type,data) => o.apply({type,request_id:'current',task_id:'t',agent_id:'analyst',ts:100,data});
  event('task.dispatched',{step_id:'analysis'});
  event('agent.tool',{id:'b',name:'Bash',input:'example'});
  assert.equal(o.stateText(a),'명령 실행 중 · 0분째');
  o.S.approvals.set('p',{id:'p',agent_id:'analyst',task_id:'t'});
  assert.equal(o.stateText(a),'PI 결정 기다리는 중');
  event('agent.log',{level:'warn',text:'No output'});
  assert.equal(o.stateText(a),'No output');
});
test('review names its request and requires participation, including CSO', () => {
  const o = fixture(); o.S.requests.delete('current');
  assert.equal(o.stateText(o.ag('analyst')), '작업 끝 · PI 검토 대기 · Previous study');
  assert.equal(o.visual(o.ag('cso')), 'idle');
  o.req('old').agent_id = 'cso';
  assert.equal(o.stateText(o.ag('cso')), '작업 끝 · PI 검토 대기 · Previous study');
  o.req('old').text = 'x'.repeat(100);
  assert.ok(o.stateText(o.ag('analyst')).length < 60);
  o.apply({type:'request.reviewed',request_id:'old',data:{pi_reviewed_at:100}});
  assert.equal(o.visual(o.ag('analyst')), 'idle');
});
test('2.5D preserves request labels and 3D consumes the same state functions', async () => {
  const ui = await import('data:text/javascript;base64,' + fs.readFileSync(path.join(web,'ui/office-art.js')).toString('base64'));
  const o = fixture(), a = o.ag('analyst');
  assert.equal(ui.officeStatus(a,o.visual(a),o.stateText(a)).text,'차례 대기');
  o.S.requests.delete('current');
  assert.equal(ui.officeStatus(a,o.visual(a),o.stateText(a)).text,o.stateText(a));
  const live = fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8');
  const main = fs.readFileSync(path.join(web,'lab3d/src/main.js'),'utf8');
  assert.match(live,/onState\(S, visual, stateText, currentTask\)/);
  assert.match(main,/c\.statusText=stateText\(a\)/);
});
