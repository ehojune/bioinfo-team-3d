const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');

// CP2 evidence review (#90): approve, revise and deny are structured choices; the note never decides.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('decision cards must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const source=fs.readFileSync(path.join(web,'ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const REFUSED=[{step_id:'s1',evidence_id:'e1',reason:'cites artifact a1 at outputs/missing.tsv'}];
const REFUSED_ROWS=[{step_id:'s1',row_type:'evidence',row_id:'e_bad',reason:'invalid accessed_at'}];
const cp2={id:'cp2',kind:'research_evidence',summary:'CP2 evidence review',created_at:1,
  detail:{gate:'research_evidence',plan_sha256:'f'.repeat(64),choices:['approve','revise','deny'],
    refused_rows:REFUSED_ROWS,refused_evidence:REFUSED,results:{s1:{claims:[],evidence:[]}}}};

test('the CP2 card offers approve, revise and deny and sends each as a choice',async()=>{
  const decide=await modulePromise,container=new Element(),sent=[];
  const [row]=decide.syncDecisionCards(container,[cp2],[],{onDecision:(a,ok,note,type,choice)=>sent.push([ok,note,choice])});
  const p=row._decisionParts;
  assert.equal(p.approve.textContent,'증거 승인');
  assert.equal(p.revise.hidden,false);
  assert.equal(p.revise.textContent,'수정 요청','a revise re-plans through a new CP1 (R10)');
  assert.match(p.consequence.textContent,/새 CP1/);
  assert.match(p.note.placeholder,/고칠 점을 꼭 적어/);
  assert.equal(p.revise.dataset.act,'revise');
  assert.equal(p.deny.textContent,'거부');
  p.note.value='Request revision';
  p.approve.onclick(); p.revise.onclick(); p.deny.onclick();
  assert.deepEqual(sent,[[true,'Request revision','approve'],[false,'Request revision','revise'],[false,'Request revision','deny']]);
});

test('past the continuation cap the revise button says the request ends (R10)',async()=>{
  const decide=await modulePromise,container=new Element();
  const ending={...cp2,id:'cp2-end',detail:{...cp2.detail,revise_continues:false}};
  const [row]=decide.syncDecisionCards(container,[ending]);
  const p=row._decisionParts;
  assert.equal(p.revise.textContent,'수정 요청(요청 끝남)');
  assert.match(p.consequence.textContent,/이어 가기 상한에 닿아 수정 요청과 거부는 둘 다 이 요청을 끝냅니다/);
  assert.doesNotMatch(row._decisionParts.detail.textContent,/revise_continues/);
});

test('a revise needs a note while it re-plans; approve, deny and an ending revise do not (R10)',async()=>{
  const decide=await modulePromise;
  assert.match(decide.revisionNoteMissing(cp2,'revise',''),/고칠 점을 메모에/);
  assert.match(decide.revisionNoteMissing(cp2,'revise','   '),/고칠 점을 메모에/);
  assert.equal(decide.revisionNoteMissing(cp2,'revise','add a sensitivity check'),'');
  assert.equal(decide.revisionNoteMissing(cp2,'approve',''),'');
  assert.equal(decide.revisionNoteMissing(cp2,'deny',''),'');
  assert.equal(decide.revisionNoteMissing({...cp2,detail:{...cp2.detail,revise_continues:false}},'revise',''),'');
  assert.equal(decide.revisionNoteMissing({id:'t',kind:'tool_permission'},'revise',''),'');
});

test('contract-refused rows and their reasons lead the CP2 card detail',async()=>{
  const decide=await modulePromise,container=new Element();
  const [row]=decide.syncDecisionCards(container,[cp2]);
  const list=row._decisionParts.detail.children[0];
  assert.equal(list.children[0].textContent,'계약에 맞지 않아 뺀 근거');
  assert.match(list.children[1].textContent,/invalid accessed_at/);
  assert.equal(list.children[2].textContent,'거부된 evidence (승인 대상 아님)');
  assert.match(list.children[3].textContent,/outputs\/missing\.tsv/);
  assert.doesNotMatch(row._decisionParts.detail.textContent,/choices/);
});

test('other approvals keep two buttons and send no choice',async()=>{
  const decide=await modulePromise,container=new Element(),sent=[];
  const [row]=decide.syncDecisionCards(container,[{id:'t',kind:'tool_permission',summary:'Bash',detail:{tool_name:'Bash'}}],[],
    {onDecision:(a,ok,note,type,choice)=>sent.push(choice)});
  assert.equal(row._decisionParts.revise.hidden,true);
  row._decisionParts.approve.onclick(); row._decisionParts.deny.onclick();
  assert.deepEqual(sent,[null,null]);
  assert.equal(decide.decisionChoice({kind:'clarify'},'approve'),null);
  assert.equal(decide.decisionChoice({kind:'research_evidence'},'revise'),'revise');
});

test('both offices forward the CP2 choice to the gateway',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  const live=fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8');
  assert.match(html,/api\.approve\(li\.dataset\.id, act === 'approve', note, decisionChoice\(\{ kind: li\.dataset\.kind \}, act\)\)/);
  assert.match(html,/choice \? \{ approved: ok, note, choice \} : \{ approved: ok, note \}/);
  assert.match(live,/choice \? \{type:'approval\.resolve', id:a\.id, approved, note, choice\}/);
  // Both refuse an empty-note revise before anything is sent (R10).
  assert.match(html,/missing = revisionNoteMissing\(approval, act, note\);\s*if \(missing\) \{ toast\(missing\); ans\.focus\(\); btn\.disabled = false; return; \}\s*\/\/ CP2 evidence review sends/);
  assert.match(live,/const missing = revisionNoteMissing\(a, choice, note\);\s*if \(missing\) \{ notice\(missing\); return; \}\s*\/\/ CP2 evidence review carries/);
  assert.match(fs.readFileSync(path.join(web,'state.js'),'utf8'),/research_evidence: 'CP2 증거 검토'/);
});
