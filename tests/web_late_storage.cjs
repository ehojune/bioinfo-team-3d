const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
require('../labhq/web/state.js');
const web = path.join(__dirname, '../labhq/web');
const mod = import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(web, 'ui/decide.js'), 'utf8')).toString('base64'));
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.dataset={}; this._text=''; }
  set textContent(v) { this._text=String(v); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  removeChild(n) { this.children=this.children.filter(c=>c!==n); }
  insertBefore(n, before) { this.children.splice(this.children.indexOf(before), 0, n); }
}
global.document={createElement:tag=>new Element(tag)};
const nodes=el=>[el,...el.children.flatMap(nodes)];
test('unreviewable completed status does not offer late storage', async()=>{
  const ui=await mod, host=new Element();
  const q={id:'r',status:'completed',report:'Result',pi_review_pending:true};
  assert.equal(LabHQState.needsStorage(q),false);
  ui.syncReviewCards(host,[q],{onStorage:()=>assert.fail('unreviewable request')});
  assert.doesNotMatch(host.textContent,/저장 위치 정하기/);
});
test('review card opens late storage and removes action after selection and reconnect', async()=>{
  const ui=await mod, host=new Element(), office=LabHQState.createOfficeState();
  office.apply({type:'snapshot',data:{requests:[{id:'r',status:'review_pending',pi_review_pending:true}]}});
  let picked;
  const options={onStorage:id=>picked=id};
  const [row]=ui.syncReviewCards(host,[office.req('r')],options);
  const button=nodes(row).find(n=>n.tagName==='button' && n.textContent==='저장 위치 정하기');
  assert.ok(button); button.onclick(); assert.equal(picked,'r');
  office.apply({type:'request.storage',request_id:'r',data:{result_storage:{mode:'new',status:'selected'}}});
  assert.doesNotMatch(ui.syncReviewCards(host,[office.req('r')],options)[0].textContent,/저장 위치 정하기/);
  office.apply({type:'snapshot',data:{requests:[{id:'r',status:'done',pi_review_pending:true,result_storage:{mode:'local',status:'selected'}}]}});
  assert.equal(office.req('r').resultStorage.mode,'local');
  assert.doesNotMatch(ui.syncReviewCards(host,[office.req('r')],options)[0].textContent,/저장 위치 정하기/);
});
test('both offices wire storage API and shared review action',()=>{
  for (const file of ['index.html','lab3d/src/live.js']) {
    const src=fs.readFileSync(path.join(web,file),'utf8');
    assert.match(src,/onStorage:/);
    assert.match(src,/\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/storage/);
  }
});
for (const presentation of ['journal', undefined]) {
  test(`storage stays reachable after review and reconnect (${presentation || '3D'})`, async()=>{
    const ui=await mod, host=new Element(), office=LabHQState.createOfficeState();
    const request={id:'r',status:'done',text:'Study',report:'Result',pi_review_pending:true};
    office.apply({type:'snapshot',data:{requests:[request]}});
    let picked;
    const options={presentation,onStorage:id=>picked=id};
    ui.syncReviewCards(host,[office.req('r')],options);
    office.apply({type:'request.reviewed',request_id:'r',data:{pi_reviewed_at:1,pi_review_pending:false}});
    const verify=()=>{
      const [row]=ui.syncReviewCards(host,[office.req('r')],options);
      assert.ok(row, 'reviewed request still offers storage');
      assert.doesNotMatch(row.textContent,/검토 완료|확인 완료|검토 대기|검토해 주세요/);
      const button=nodes(row).find(n=>n.tagName==='button' && n.textContent==='저장 위치 정하기');
      assert.ok(button); button.onclick(); assert.equal(picked,'r');
    };
    verify();
    office.apply({type:'snapshot',data:{requests:[{...request,pi_reviewed_at:1,pi_review_pending:false}]}});
    verify();
    office.apply({type:'request.storage',request_id:'r',data:{result_storage:{mode:'local'}}});
    assert.equal(ui.syncReviewCards(host,[office.req('r')],options).length,0);
  });
}
test('2.5D decision render includes reviewed storage requests in cards and badge',()=>{
  const src=fs.readFileSync(path.join(web,'index.html'),'utf8');
  const render=src.slice(src.indexOf('function renderApprovals() {'),src.indexOf("  $('#ap-count').textContent"));
  const q={id:'r',status:'done',pi_reviewed_at:1};
  let cards, badge;
  // Run the extracted renderer with the same dependencies as the page.
  const run=new Function('S','LabHQState','$','updateDecisionBadge','syncReviewCards','openReview','finishReview','chooseStorage','api',render+'}; return renderApprovals;');
  run({approvals:new Map(),suggestions:[],engineHolds:new Map(),requests:new Map([['r',q]])},LabHQState,()=>({}),
    (_,n)=>badge=n,(_,qs)=>cards=qs,()=>{},()=>{},()=>{},{reviewDone:true})();
  assert.deepEqual(cards,[q]); assert.equal(badge,1);
});
test('reconciled storage replay closes the card without claiming a PI refusal',()=>{
  const office=LabHQState.createOfficeState();
  office.apply({type:'approval.requested',request_id:'r',data:{id:'a',kind:'result_storage',request_id:'r'}});
  office.apply({type:'request.storage',request_id:'r',data:{result_storage:{mode:'local'}}});
  office.apply({type:'approval.resolved',request_id:'r',data:{id:'a',state:'reconciled'}});
  assert.equal(office.S.approvals.size,0);
  assert.equal(office.req('r').resultStorage.mode,'local');
  assert.equal(office.S.feed[0].text,'저장 선택을 복구했어요');
  assert.equal(office.S.feed[0].who,'system');
});
test('cancelled partial reports retain the late storage action', async()=>{
  const ui=await mod, host=new Element();
  const q={id:'r',status:'cancelled',report:'Partial result',pi_review_pending:true};
  assert.equal(LabHQState.needsStorage(q),true);
  let picked;
  const [row]=ui.syncReviewCards(host,[q],{onStorage:id=>picked=id});
  const button=nodes(row).find(n=>n.tagName==='button' && n.textContent==='저장 위치 정하기');
  assert.ok(button); button.onclick(); assert.equal(picked,'r');
  assert.equal(LabHQState.needsStorage({...q,resultStorage:{mode:'local'}}),false);
  for (const report of [null,'','   ']) assert.equal(LabHQState.needsStorage({...q,report}),false);
});
test('3D review passes the CP4 target before late storage publishing', async()=>{
  const src=fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8');
  const handler=src.slice(src.indexOf('  async function reviewDone('),src.indexOf('  function openReview('));
  const final={revision:2,target_sha256:'draft-hash'}, calls=[];
  const S={requests:new Map([['r',{status:'review_pending',final_checkpoint:final}]])};
  const fetch=async(url,options)=>{calls.push([url,options]);return {ok:true,json:async()=>({})};};
  const review=new Function('S','fetch','token','apply','render','notice',handler+'; return reviewDone;')
    (S,fetch,'fake-token',()=>{},()=>{},()=>{});
  await review('r');
  assert.deepEqual(JSON.parse(calls[0][1].body),{decision:'accepted',...final});
  await review('r',{decision:'rejected',...final});
  assert.equal(JSON.parse(calls[1][1].body).decision,'rejected');
});
