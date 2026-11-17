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
  const decide=await modulePromise,container=new Element(),sent=[],refusalChoices=[];
  const [row]=decide.syncDecisionCards(container,[cp2],[],{onDecision:(a,ok,note,type,choice,storage,refusal)=>{
    sent.push([ok,note,choice]); refusalChoices.push(refusal);
  }});
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
  assert.equal(decide.modelRefusalChoice(row),null,'CP2 never offers model-refusal recovery');
  assert.deepEqual(refusalChoices,[null,undefined,undefined],'CP2 decisions carry no model-refusal payload');
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

test('missing artifact reference warning is Korean and retains the original in details',async()=>{
  const decide=await modulePromise,container=new Element();
  const item={...cp2,detail:{...cp2.detail,refused_evidence:[{evidence_id:'e1',reason:'evidence e1 cites artifact a1 missing from artifact_refs'}]}};
  const [row]=decide.syncDecisionCards(container,[item]);
  const detail=row._decisionParts.detail.textContent;
  assert.match(detail,/근거 e1가 가리킨 산출 파일이 등록 목록에 없습니다/);
  assert.match(detail,/missing from artifact_refs/);
});

test('journal shows a scoped grant button only for eligible tool cards',async()=>{
  const decide=await modulePromise,container=new Element();
  const card={id:'tool',kind:'tool_permission',request_id:'r1',detail:{grant_category:'workspace',policy_reason:'private_paths'}};
  const [row]=decide.syncDecisionCards(container,[card],[],{presentation:'journal'});
  assert.equal(row._decisionParts.grant.hidden,false);
  assert.equal(row._decisionParts.grant.dataset.act,'grant');
  assert.match(row._decisionParts.grantExplanation.textContent,/코드가 실행 중에 하는 일/);
  assert.doesNotMatch(row._decisionParts.reason.textContent,/private_paths/);
  decide.syncDecisionCards(container,[{...card,detail:{}}],[],{presentation:'journal'});
  assert.equal(row._decisionParts.grant.hidden,true);
});

test('journal keeps the tool risk grade visible even when the reason is summarized',async()=>{
  const decide=await modulePromise,container=new Element();
  const card={id:'tool-risk',kind:'tool_permission',request_id:'r1',detail:{policy_reason:'private_paths'},
    pi_reason:'작업 목적: 분석.\n걸린 규칙: 개인 경로 보호.\n위험 등급: 높음 · 개인 정보 노출 확인 필요.'};
  const [row]=decide.syncDecisionCards(container,[card],[],{presentation:'journal'});
  assert.match(row._decisionParts.facts.textContent,/위험 등급높음/);
  decide.syncDecisionCards(container,[{...card,pi_reason:''}],[],{presentation:'journal'});
  assert.doesNotMatch(row._decisionParts.facts.textContent,/위험 등급/);
});

test('failed tool translation stays hidden and raw policy reason stays in details',async()=>{
  const decide=await modulePromise,container=new Element();
  const card={id:'tool-failed',kind:'tool_permission',request_id:'r',summary:'English source',pi_summary:'현재 작업 폴더: 파일 목록 보기.',pi_display:{status:'failed'},
    pi_reason:'위험 등급: 높음 · 개인 경로',detail:{tool_name:'Bash',grant_category:'unresolved_path',policy_reason:'Bash read scope is ambiguous (policy.private_paths)'}};
  const [row]=decide.syncDecisionCards(container,[card],[],{presentation:'journal'});
  const p=row._decisionParts;
  assert.match(p.reason.textContent,/경로를 미리 확인하지 못했습니다/);
  assert.doesNotMatch(p.summary.textContent,/번역 못 함|English source/);
  assert.match(p.summary.textContent,/파일 목록 보기/);
  assert.match(p.facts.textContent,/위험 등급높음 · 개인 경로/);
  assert.match(p.detail.textContent,/Bash read scope is ambiguous/);
  decide.syncDecisionCards(container,[{...card,pi_display:{status:'translated',text:'분석 코드를 실행하려 합니다.'}}],[],{presentation:'journal'});
  assert.match(p.summary.textContent,/분석 코드를 실행하려 합니다/);
});

test('tool card retains server purpose, step, reason and risk',async()=>{
  const decide=await modulePromise,container=new Element();
  const card={id:'tool-context',kind:'tool_permission',request_id:'r',pi_reason:'작업 목적: 비교 분석. 단계: s2.\n걸린 규칙: PI 개인 경로 보호.\n위험 등급: 높음 · 개인 정보',
    detail:{tool_name:'Bash',grant_category:'unresolved_path',policy_reason:'Bash read scope is ambiguous (policy.private_paths)'}};
  const [row]=decide.syncDecisionCards(container,[card],[],{presentation:'journal'});
  assert.match(row._decisionParts.reason.textContent,/작업 목적: 비교 분석.*단계: s2/);
  assert.match(row._decisionParts.reason.textContent,/걸린 규칙: PI 개인 경로 보호/);
  assert.doesNotMatch(row._decisionParts.reason.textContent,/위험 등급: 높음/);
  assert.match(row._decisionParts.facts.textContent,/위험 등급높음 · 개인 정보/);
});

test('3D tool card shows the server reason and risk',async()=>{
  const decide=await modulePromise,container=new Element();
  const card={id:'tool-3d',kind:'tool_permission',pi_reason:'작업 목적: 비교 분석.\n작업 폴더 안에서 파일을 만들거나 고칩니다.\n위험 등급: 중간 · 작업 폴더 안 쓰기.',
    detail:{tool_name:'Bash',grant_category:'workspace',policy_reason:'PI personal path'}};
  const [row]=decide.syncDecisionCards(container,[card],[]);
  assert.match(row._decisionParts.detail.textContent,/작업 폴더 안에서 파일을 만들거나 고칩니다/);
  assert.match(row._decisionParts.detail.textContent,/위험 등급: 중간 · 작업 폴더 안 쓰기/);
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
  // The whole card goes in, so CP1 sends 수정 요청 only where detail.revise_allowed offers it.
  assert.match(html,/api\.approve\(li\.dataset\.id, act === 'approve', note, decisionChoice\(approval \|\| \{ kind: li\.dataset\.kind \}, act\), storageChoice\(li\), modelRefusalChoice\(li\)\)/);
  assert.match(html,/choice \? \{ approved: ok, note, choice \} : \{ approved: ok, note \}/);
  assert.match(live,/choice \? \{type:'approval\.resolve', id:a\.id, approved, note, choice\}/);
  // Both refuse an empty-note revise before anything is sent (R10).
  assert.match(html,/missing = revisionNoteMissing\(approval, act, note\);\s*if \(missing\) \{ toast\(missing\); ans\.focus\(\); btn\.disabled = false; return; \}\s*\/\/ CP2 evidence review sends/);
  assert.match(live,/const missing = revisionNoteMissing\(a, choice, note\);\s*if \(missing\) \{ notice\(missing\); return; \}\s*\/\/ CP2 evidence review carries/);
  assert.match(fs.readFileSync(path.join(web,'state.js'),'utf8'),/research_evidence: 'CP2 증거 검토'/);
});

const cp1={id:'cp1',kind:'research_plan',summary:'CP1 연구 계획 승인',created_at:1,
  detail:{gate:'research_plan',target_sha256:'e'.repeat(64),revise_allowed:true,plan_canonical:'{}'}};

test('CP1 offers 수정 요청 that needs a note and opens a new request; a continuation CP1 does not',async()=>{
  const decide=await modulePromise,container=new Element(),sent=[];
  const [row]=decide.syncDecisionCards(container,[cp1],[],{onDecision:(a,ok,note,type,choice)=>sent.push([ok,choice])});
  const p=row._decisionParts;
  assert.equal(p.approve.textContent,'승인');
  assert.equal(p.revise.hidden,false);
  assert.equal(p.revise.textContent,'수정 요청');
  assert.match(p.consequence.textContent,/새 요청으로 계획을 다시 세워 새 CP1/);
  assert.match(p.note.placeholder,/고칠 점을 꼭 적어/);
  assert.doesNotMatch(p.detail.textContent,/revise_allowed/);
  p.approve.onclick(); p.revise.onclick(); p.deny.onclick();
  assert.deepEqual(sent,[[true,'approve'],[false,'revise'],[false,'deny']]);
  assert.match(decide.revisionNoteMissing(cp1,'revise',' '),/고칠 점을 메모에/);
  assert.match(decide.revisedToast(cp1),/새 요청으로 계획을 다시/);
  // A continuation round's CP1 (or an older gateway's card without the flag) keeps 승인·거절 and sends no choice.
  for (const detail of [{...cp1.detail,revise_allowed:false},{gate:'research_plan',plan_canonical:'{}'}]) {
    const old=[];
    const [plain]=decide.syncDecisionCards(new Element(),[{...cp1,id:'cp1b',detail}],[],{onDecision:(a,ok,note,type,choice)=>old.push(choice)});
    assert.equal(plain._decisionParts.revise.hidden,true);
    assert.match(plain._decisionParts.consequence.textContent,/거절하면 이 요청은 단계를 돌리지 않고 끝납니다/);
    plain._decisionParts.approve.onclick(); plain._decisionParts.deny.onclick();
    assert.deepEqual(old,[null,null]);
    assert.equal(decide.revisionNoteMissing({...cp1,detail},'revise',''),'');
  }
});
