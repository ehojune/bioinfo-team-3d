const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// PI readiness 2026-10-08 (R10 R14 R18 R19 R21): request status labels, resume events, CP1/CP2 cards from a real
// trial card, question and resume cards, and hold visibility. textContent only: any HTML insertion fails.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text='';this.hidden=false; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('cards must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
  insertBefore(child,ref) { this.removeChild(child);this.children.splice(this.children.indexOf(ref),0,child);return child; }
  removeChild(child) { this.children=this.children.filter(x=>x!==child);return child; }
  addEventListener(type,callback) { this['on'+type]=callback; }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const load=name=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(web,'ui',name),'utf8')).toString('base64'));
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(web,'state.js'),'utf8'),context);
const State=context.LabHQState;
const nodes=el=>[el,...el.children.flatMap(nodes)];
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/research_cards_req_7ccde78be0.json'),'utf8'));

test('every request status has its own label and interrupted never reads 실패 (R14)',()=>{
  const want={running:'진행 중',waiting_for_runner:'러너 기다림',waiting_quota:'한도 대기',waiting_login:'로그인 대기',
    waiting_facilities_fix:'환경 수정 승인 대기',interrupted:'중단됨',done:'완료',failed:'실패',cancelled:'취소됨',rejected:'거부됨'};
  for (const [status,label] of Object.entries(want)) assert.equal(State.requestStatusLabel(status),label,status);
  assert.equal(State.requestStatusLabel('some_new_state'),'some_new_state','an unknown status is shown as itself');
  const approvals=new Map([['a',{id:'a',kind:'resume',request_id:'r1'}]]);
  assert.equal(State.requestStatusText({id:'r1',status:'interrupted'},approvals),'중단됨 · 결정 탭의 재개 카드로 이어 가요');
  assert.equal(State.requestStatusText({id:'r2',status:'interrupted'},approvals),'중단됨');
  assert.equal(State.requestStatusText({id:'r1',status:'waiting_for_runner',resumeMissing:['analyst','engineer']},approvals),
    '러너 기다림 · 연결 필요: analyst, engineer');
});

test('a resumed request follows resume events without a reload (R14)',()=>{
  const office=State.createOfficeState({now:()=>100});
  const plan={steps:[{id:'s1',agent_id:'analyst'},{id:'s2',agent_id:'engineer',depends_on:['s1']}]};
  const resume={id:'appr_r',kind:'resume',request_id:'r1',summary:"중단된 단계 ['s2']를 다시 돌릴까요?",created_at:90,timeout_s:3600};
  office.apply({type:'snapshot',data:{requests:[{id:'r1',text:'짝 DE',status:'interrupted',plan,step_status:{s1:'done'}}],approvals:[resume]}});
  const q=office.S.requests.get('r1');
  assert.equal(q.status,'interrupted');assert.equal(q.phase,'done');
  office.apply({type:'approval.resolved',request_id:'r1',data:{id:'appr_r',approved:true}});
  assert.equal(q.status,'waiting_for_runner','an approved resume card waits for runners at once');
  assert.equal(q.phase,'execute','the phase leaves done');
  office.apply({type:'request.resume_waiting',request_id:'r1',data:{missing_agents:['engineer']}});
  assert.equal(State.requestStatusText(q,office.S.approvals),'러너 기다림 · 연결 필요: engineer');
  assert.match(office.S.feed[0].text,/engineer/);
  office.apply({type:'request.resumed',request_id:'r1',data:{agents:['engineer']}});
  assert.equal(q.status,'running');assert.deepEqual([...q.resumeMissing],[]);
  office.apply({type:'request.resume_timeout',request_id:'r1',data:{missing_agents:['engineer']}});
  assert.equal(q.status,'interrupted','a resume timeout is interrupted again, not failed');
  assert.ok(!JSON.stringify(office.S).includes('resumeMissing'),'the new field stays out of the legacy state digest');
});

test('a page left open across a gateway restart learns the request is interrupted from its resume card (R14)',()=>{
  const office=State.createOfficeState({now:()=>100});
  office.apply({type:'request.created',request_id:'r1',data:{text:'요청',mode:'orchestrate'}});
  office.apply({type:'request.created',request_id:'r2',data:{text:'끝난 요청',mode:'orchestrate'}});
  office.apply({type:'request.completed',request_id:'r2',data:{ok:true}});
  for (const rid of ['r1','r2']) office.apply({type:'approval.requested',request_id:rid,
    data:{id:`appr_${rid}`,kind:'resume',request_id:rid,summary:"중단된 단계 ['요청']를 다시 돌릴까요?"}});
  assert.equal(office.S.requests.get('r1').status,'interrupted');
  assert.equal(office.S.requests.get('r2').status,'done','a finished request is never reopened by a card');
  office.apply({type:'approval.resolved',request_id:'r1',data:{id:'appr_r1',approved:false}});
  assert.equal(office.S.requests.get('r1').status,'interrupted','a declined resume waits for request.failed');
  office.apply({type:'request.failed',request_id:'r1',data:{error:'resume declined'}});
  assert.equal(office.S.requests.get('r1').status,'failed');
});

test('an alert log raises a toast once, never on replay (R21)',()=>{
  const office=State.createOfficeState({now:()=>100});
  office.apply({type:'snapshot',data:{agents:[{id:'engineer',name:'Engineer Kim'}]}});
  const text='codex 출력이 12분째 없고 이 PC에 Windows 권한 알림(UAC)이 승인을 기다립니다.';
  const effects=office.apply({type:'agent.log',agent_id:'engineer',data:{level:'alert',text}});
  assert.equal(effects.length,1);assert.equal(effects[0].type,'toast');assert.match(effects[0].text,/^Engineer: .*UAC/);
  assert.equal(office.apply({type:'agent.log',agent_id:'engineer',data:{level:'alert',text}},true).length,0);
  assert.equal(office.apply({type:'agent.log',agent_id:'engineer',data:{level:'info',text:'진행 중'}}).length,0);
});

test('a quota wait shows its date and the last deadline (R21)',async()=>{
  const office=State.createOfficeState({now:()=>100});
  office.apply({type:'snapshot',data:{requests:[{id:'q',status:'running',plan:{steps:[{id:'s1',agent_id:'engineer'}]}}]}});
  const resumeAt=Date.UTC(2026,9,12,3,0)/1000,deadline=Date.UTC(2026,9,15,3,0)/1000;
  office.apply({type:'request.step_quota_wait',request_id:'q',data:{step_id:'s1',engine:'codex',resume_at:resumeAt,deadline_at:deadline}});
  const tasks=await load('tasks.js'),board=new Element();
  tasks.syncTaskBoard(board,office.S.requests.get('q'),office.S.stepDetails,{});
  const line=nodes(board).find(n=>/^한도 대기 · /.test(n._text));
  assert.equal(line._text,`한도 대기 · ${tasks.stamp(resumeAt)} 재개 · 최대 ${tasks.stamp(deadline)}까지 기다림`);
  assert.match(tasks.stamp(resumeAt),/^10-1[12] \d\d:\d\d$/,'month and day are shown');
  office.apply({type:'request.step_quota_resumed',request_id:'q',data:{step_id:'s1'}});
  assert.equal(office.S.stepDetails.get('q:s1').quota_deadline_at,undefined);
  office.apply({type:'request.step_quota_wait',request_id:'q',data:{step_id:'s1',engine:'codex',resume_at:resumeAt}});
  tasks.syncTaskBoard(board,office.S.requests.get('q'),office.S.stepDetails,{});
  assert.equal(nodes(board).find(n=>/^한도 대기 · /.test(n._text))._text,`한도 대기 · ${tasks.stamp(resumeAt)} 재개`,
    'a snapshot without the deadline shows the date only');
});

test('the CP2 card of req_7ccde78be0 is a 22-row claim table with the JSON folded (R18, R10)',async()=>{
  const decide=await load('decide.js'),container=new Element(),sent=[];
  const requests=new Map([[fixture.request.id,{text:fixture.request.text,plan:[]}]]);
  const [row]=decide.syncDecisionCards(container,[fixture.research_evidence],[],{requests,now:()=>fixture.research_evidence.created_at+60,
    onDecision:(a,ok,note,type,choice)=>sent.push(choice)});
  const p=row._decisionParts;
  assert.match(p.request.textContent,/^요청: GEO GSE19804는/,'the request text leads the card');
  assert.equal(p.request.hidden,false);
  const table=nodes(p.detail).find(n=>n.tagName==='TABLE');
  assert.ok(table,'claims render as a table');
  assert.deepEqual(table.children[0].children[0].children.map(c=>c.textContent),['단계','claim','상태','근거 종류','표시']);
  const rows=table.children[1].children;
  assert.equal(rows.length,22,'one row per claim');
  const cells=r=>r.children.map(c=>c.textContent);
  const first=cells(rows[0]);
  assert.equal(first[0],'fetch_data');assert.match(first[1],/계획된 공개 자료를 모두 받았다/);assert.match(first[1],/c_data_obtained$/);
  assert.equal(first[2],'지지');assert.match(first[3],/관찰/);
  const flagged=rows.filter(r=>r.className==='flagged').map(cells);
  assert.deepEqual(flagged.map(c=>[c[0],c[4]]),[['qc_data','거부된 근거 e_verdict, 근거 잃음']],'refused evidence and the claim it left unsupported are marked');
  assert.ok(rows.map(cells).some(c=>c[2]==='부분 지지'),'claim statuses are Korean');
  const folds=nodes(p.detail).filter(n=>n.tagName==='DETAILS');
  const ledger=folds.find(f=>/^원장 JSON 전체/.test(f.children[0].textContent));
  assert.ok(ledger&&!ledger.open,'the raw ledger JSON stays folded');
  assert.match(ledger.children[1].textContent,/"c_data_obtained"/);
  const hashes=folds.find(f=>/^산출 파일 hash/.test(f.children[0].textContent));
  assert.ok(hashes&&!hashes.open);
  const labels=nodes(p.detail).filter(n=>n.tagName==='DT').map(n=>n.textContent);
  assert.deepEqual(labels.slice(0,3),['거부된 evidence (승인 대상 아님)','근거를 잃은 claim','claim별 근거 (22개)']);
  assert.ok(!labels.includes('results')&&!labels.includes('choices')&&!labels.includes('gate'));
  assert.equal(p.revise.hidden,false);assert.equal(p.revise.textContent,'수정 요청(요청 끝남)');
  assert.match(p.consequence.textContent,/수정 요청과 거부는 둘 다 이 요청을 끝냅니다/);assert.equal(p.consequence.hidden,false);
  assert.match(p.timing.textContent,/1분 대기 · 59분 남음/,'CP2 keeps its real countdown');
  p.revise.onclick();assert.deepEqual(sent,['revise'],'the revise choice is still sent unchanged');
});

test('the CP1 card of req_7ccde78be0 lists steps once and folds the protocol (R18)',async()=>{
  const decide=await load('decide.js'),container=new Element();
  const [row]=decide.syncDecisionCards(container,[fixture.research_plan]);
  const p=row._decisionParts,labels=nodes(p.detail).filter(n=>n.tagName==='DT').map(n=>n.textContent);
  for (const raw of ['gate','pack_applicability','warnings','protocol_revision','packs','target_sha256','scope_status'])
    assert.ok(!labels.includes(raw),`raw key ${raw} is not repeated after the plan view`);
  assert.equal(labels.filter(l=>l==='pack 적용 판정').length,1);
  assert.ok(!labels.includes('경고'),'an empty warning list is not shown');
  assert.ok(labels.includes('plan hash')&&labels.includes('범위 판정'));
  const plan=JSON.parse(fixture.research_plan.detail.plan_canonical);
  const steps=nodes(p.detail).find(n=>n.tagName==='DETAILS'&&/^단계 12개/.test(n.children[0].textContent));
  assert.ok(steps&&steps.open,'the step list is open by default');
  const text=steps.children[1].textContent;
  for (const step of plan.steps) {
    assert.match(text,new RegExp(`^${step.id} · ${step.agent_id} · `,'m'),step.id);
    assert.ok(text.includes(step.outputs[0]),`${step.id} outputs`);
  }
  const protocol=nodes(p.detail).find(n=>n.tagName==='DETAILS'&&/^protocol 전체/.test(n.children[0].textContent));
  assert.ok(protocol&&!protocol.open,'the protocol is folded');
  assert.match(p.detail.textContent,/bulk_tumor_normal@3: 적용 · topic_match · microarray_expression/);
  assert.match(p.detail.textContent,/주 가설: 같은 환자 안에서/);
  assert.ok(!/"primary"/.test(p.detail.textContent.split('protocol 전체')[0]),'hypotheses are text, not a JSON object');
  assert.match(p.consequence.textContent,/거절하면 이 요청은 단계를 돌리지 않고 끝납니다/);
});

test('KIND_KO names resume and question cards (R19)',()=>{
  const {KIND_KO}=State.createOfficeState();
  assert.equal(KIND_KO.resume,'중단된 요청 재개');
  assert.equal(KIND_KO.question,'직원 질문 · PI 확인');
});

test('a resume card joins step ids, has no countdown and says what 거절 does (R19)',async()=>{
  const decide=await load('decide.js'),container=new Element();
  const card={id:'appr_r',kind:'resume',request_id:'r1',summary:"중단된 단계 ['s2', 's3']를 다시 돌릴까요?",created_at:1,timeout_s:3600};
  const requests=new Map([['r1',{text:'새로 받은 WGS 배치 표준 QC',plan:[]}]]);
  const [row]=decide.syncDecisionCards(container,[card],[],{requests,now:()=>1+14239*60,kindLabels:State.createOfficeState().KIND_KO});
  const p=row._decisionParts;
  assert.equal(p.title.textContent,'중단된 요청 재개');
  assert.equal(p.summary.textContent,'중단된 단계(s2, s3)를 다시 돌릴까요?');
  assert.equal(p.request.textContent,'요청: 새로 받은 WGS 배치 표준 QC');
  assert.equal(p.timing.textContent,'14239분 대기','the gateway never times a resume card out');
  assert.equal(p.consequence.textContent,'거절하면 이 요청은 실패로 끝납니다.');
  assert.equal(decide.displaySummary({...card,summary:"중단된 단계 ['요청']를 다시 돌릴까요?"}),'중단된 요청을 다시 이어 갈까요?');
  assert.equal(decide.displaySummary({kind:'tool_permission',summary:"['a']"}),"['a']",'other kinds keep their text');
  const history=new Element();
  decide.syncDecisionHistory(history,[{approval:card,approved:true}],{requests});
  assert.match(history.textContent,/중단된 단계\(s2, s3\)를 다시 돌릴까요\?/);
  const tool=decide.syncDecisionCards(new Element(),[{id:'t',kind:'tool_permission',request_id:'r1',summary:'Bash',created_at:1,timeout_s:90}],[],
    {requests,now:()=>31})[0]._decisionParts;
  assert.equal(tool.request.textContent,'요청: 새로 받은 WGS 배치 표준 QC');
  assert.equal(tool.timing.textContent,'30초 대기 · 1분 남음');
  assert.equal(tool.consequence.hidden,true,'no consequence line where the outcome is not certain');
});

test('a hard-stop question card renders its options as choices and needs one (R19)',async()=>{
  const decide=await load('decide.js'),container=new Element(),sent=[];
  const card={id:'appr_q',kind:'question',request_id:'r1',agent_id:'engineer',summary:'Hard stop (install): scanpy를 설치할까요?',
    detail:{ask_id:'ask_1',from:'engineer',why_blocked:'scanpy가 없습니다',options:['공유 환경에 설치','내 폴더에만 설치','설치하지 않음']}};
  const [row]=decide.syncDecisionCards(container,[card],[],{onDecision:(a,ok,note)=>sent.push([ok,note])});
  const p=row._decisionParts;
  assert.equal(p.questions.hidden,false);
  const buttons=nodes(p.questions).filter(n=>n.tagName==='BUTTON');
  assert.deepEqual(buttons.map(b=>b.textContent),['a) 공유 환경에 설치','b) 내 폴더에만 설치','c) 설치하지 않음']);
  assert.ok(!/\["공유/.test(p.detail.textContent),'options are not dumped as JSON');
  assert.match(p.detail.textContent,/막힌 이유scanpy가 없습니다/);
  assert.equal(p.approve.textContent,'답하고 진행');
  assert.equal(decide.answerRequired(card),true);
  assert.equal(decide.decisionNote(row,true),'','no choice yet: nothing to send');
  buttons[1].onclick();
  assert.equal(decide.decisionNote(row,true),'b) 내 폴더에만 설치','the employee gets the choice itself');
  p.note.value='tmp 아래로';
  assert.equal(decide.decisionNote(row,true),'b) 내 폴더에만 설치\n메모: tmp 아래로');
  assert.equal(decide.decisionNote(row,false),'tmp 아래로');
  p.approve.onclick();assert.deepEqual(sent,[[true,'b) 내 폴더에만 설치\n메모: tmp 아래로']]);
  assert.equal(p.consequence.textContent,'거절하면 직원에게 "진행 불가"로 전합니다.');
  const plain={...card,id:'appr_p',detail:{ask_id:'ask_2',from:'engineer',why_blocked:'w',options:[]}};
  const [open]=decide.syncDecisionCards(new Element(),[plain]);
  assert.equal(decide.answerRequired(plain),false,'a question without choices may be approved as is');
  assert.equal(open._decisionParts.questions.hidden,true);
  assert.equal(open._decisionParts.approve.textContent,'승인');
});

test('a hard-stop question with a single option still needs that option picked (PR #494 review)',async()=>{
  const decide=await load('decide.js'),container=new Element(),sent=[];
  // AskRequest.options accepts a one-item list; dropping it would let 승인 send "승인했지만 답변을 남기지 않았습니다".
  const card={id:'appr_one',kind:'question',request_id:'r1',agent_id:'engineer',summary:'Hard stop (delete): tmp/를 지울까요?',
    detail:{ask_id:'ask_3',from:'engineer',why_blocked:'디스크가 찼습니다',options:['tmp/만 지우기']}};
  const [row]=decide.syncDecisionCards(container,[card],[],{onDecision:(a,ok,note)=>sent.push([ok,note])});
  const p=row._decisionParts,buttons=nodes(p.questions).filter(n=>n.tagName==='BUTTON');
  assert.equal(p.questions.hidden,false);
  assert.deepEqual(buttons.map(b=>b.textContent),['a) tmp/만 지우기']);
  assert.equal(decide.answerRequired(card),true,'the single option must be chosen before 답하고 진행');
  assert.equal(p.approve.textContent,'답하고 진행');
  assert.ok(!/\["tmp/.test(p.detail.textContent),'the option is a button, not JSON');
  assert.equal(decide.decisionNote(row,true),'','no choice yet: nothing to send');
  buttons[0].onclick();
  assert.equal(decide.decisionNote(row,true),'a) tmp/만 지우기');
  p.approve.onclick();assert.deepEqual(sent,[[true,'a) tmp/만 지우기']]);
  const clarify={id:'c1',kind:'clarify',summary:'s',detail:{questions:[{question:'Which?',options:['only one'],allow_free_text:false}]}};
  const [plain]=decide.syncDecisionCards(new Element(),[clarify]);
  assert.equal(nodes(plain._decisionParts.questions).filter(n=>n.tagName==='BUTTON').length,0,'clarify keeps its two-option rule');
});

test('both offices use the shared labels, guards and request lookup',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  const live=fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8');
  assert.doesNotMatch(html,/q\.status === 'done' \? '완료' : '실패'/,'no status falls through to 실패');
  assert.match(html,/LabHQState\.requestStatusText\(q, S\.approvals\)/);
  assert.match(html,/LabHQState\.requestStatusLabel\(r\.status\)/);
  assert.match(html,/li\.dataset\.kind === 'question' && act === 'approve' && !note && answerRequired\(/);
  assert.match(html,/\.evidence-table/);
  assert.match(live,/requestStatusText\(q, S\.approvals\)/);
  assert.doesNotMatch(live,/`\$\{q\.status\} · /,'3D shows no raw status');
  assert.match(live,/requests:S\.requests/,'3D cards get the request text and blocked-step count');
  assert.match(live,/a\.kind === 'question' && approved && !note && answerRequired\(a\)/);
  assert.match(fs.readFileSync(path.join(web,'lab3d/index.html'),'utf8'),/\.evidence-table/);
});
