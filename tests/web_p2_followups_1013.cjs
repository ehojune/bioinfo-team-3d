const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const web = path.join(__dirname, '../labhq/web');
require(path.join(web, 'state.js'));
const load = () => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(web,'ui/messenger.js'),'utf8')).toString('base64'));

test('startup visibility warning reaches feed without roster', () => {
  const {S,apply} = global.LabHQState.createOfficeState();
  apply({type:'agent.log',agent_id:'cso',data:{level:'warn',code:'dev_log_visibility',text:'Repository is public'}});
  assert.equal(S.feed.at(-1)?.text,'Repository is public');
});

test('dispatch history retains team delegation and direct assignment', async () => {
  const {messageFromEvent} = await load();
  for (const [kind,who] of [['step','cso'],['direct','pi'],['briefing','cso'],['review','cso']]) {
    const m=messageFromEvent({type:'task.dispatched',agent_id:'worker',data:{kind,title:'Analyze',prompt:'private prompt'}});
    assert.equal(m?.who,who); assert.equal(m.to,'worker'); assert.ok(m.text);
  }
  assert.equal(messageFromEvent({type:'task.dispatched',data:{kind:'consult'}}),null);
});

test('REST followup owner survives live completion and reverse page order', async () => {
  const {createMessenger} = await load();
  const h=createMessenger({fetchPage:async()=>({events:[
    {seq:2,type:'request.followup_done',data:{id:'f0',ok:true,answer:'done'}},
    {seq:1,type:'request.followup',data:{id:'f0',agent_id:'analyst'}},
    {seq:3,type:'request.followup',data:{id:'f1',agent_id:'worker'}}],has_more:false})});
  await h.loadOlder();
  h.ingest({seq:4,type:'request.followup_done',data:{id:'f1',ok:true,answer:'because'}});
  assert.equal(h.items.find(x=>x.seq===2).who,'analyst');
  assert.equal(h.items.at(-1).who,'worker');
});

test('3D sync chip includes review-only requests', () => {
  const src=fs.readFileSync(path.join(web,'lab3d/src/main.js'),'utf8');
  const assignment=src.match(/liveApprovalCount=S\.approvals\.size[^;]+;/)[0];
  const S={approvals:new Map(),engineHolds:new Map(),requests:new Map([['r',{status:'done',pi_review_pending:true}]])};
  const count=Function('S',`let liveApprovalCount; ${assignment} return liveApprovalCount;`)(S);
  assert.equal(count,1);
});
