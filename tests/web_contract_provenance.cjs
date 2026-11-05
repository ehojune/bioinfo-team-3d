const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

// #424: the staff card, the hiring feed line and the 채용 제안 card show what a contract hire was built from and where
// its tools send data. textContent only: any HTML insertion fails.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this._text='';this.hidden=false;this.className=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('cards must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
  insertBefore(child,ref) { this.removeChild(child);this.children.splice(this.children.indexOf(ref),0,child);return child; }
  removeChild(child) { this.children=this.children.filter(x=>x!==child);return child; }
  setAttribute(name,value) { this[name]=String(value); }
  addEventListener(type,callback) { this['on'+type]=callback; }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const load=name=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(web,'ui',name),'utf8')).toString('base64'));
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(web,'state.js'),'utf8'),context);
const State=context.LabHQState;
const nodes=el=>[el,...el.children.flatMap(nodes)];
const COMMIT='0123456789abcdef0123456789abcdef01234567';
const provenance={source_commit:COMMIT,code_license:'BSD-3-Clause',weights_license:null,skill_sha256:'a'.repeat(64),
  mcp_sha256:null,external_services:[{host:'api.example.org',what_is_sent:'gene symbols'}],rehire_changed:[]};
const hire={id:'c_scanpy',name:'scanpy 파견',role:'[파견] 전처리',employment:'contract',contract_provenance:provenance};

test('a contract staff card shows commit, licenses and external services; a core card shows none',async()=>{
  const strip=await load('strip.js');
  const container=new Element();
  const [contract,core]=strip.syncStaffCards(container,[hire,{id:'analyst',name:'Analyst',role:'분석'}],{visual:()=> 'idle'});
  const line=nodes(contract).find(el=>el.className==='staff-provenance');
  assert.ok(line&&!line.hidden,'contract card has a provenance line');
  assert.match(line.textContent,/commit 0123456/);assert.match(line.textContent,/BSD-3-Clause/);
  assert.match(line.textContent,/외부 전송 api\.example\.org/);
  assert.ok(!line.textContent.includes(COMMIT),'a short commit fits the card');
  const coreLine=nodes(core).find(el=>el.className==='staff-provenance');
  assert.ok(!coreLine||coreLine.hidden,'core staff have no provenance line');
  assert.match(strip.provenanceText({contract_provenance:{...provenance,external_services:[],code_license:null,rehire_changed:['skill']}}),
    /commit 0123456 · license 미상 · 외부 전송 없음 · 재고용 때 바뀜: skill/);
  assert.equal(strip.provenanceText({id:'old',employment:'contract'}),'','a roster entry from an older runner shows nothing');
  // PR #522 review: a probation hire stays visible as 수습 but gets no work until activated.
  assert.match(strip.provenanceText({contract_status:'probation',contract_provenance:provenance}),/^수습\(PI 승인 대기\) · commit 0123456/);
  assert.equal(strip.provenanceText({employment:'contract',contract_status:'probation'}),'수습(PI 승인 대기)');
});

test('the hiring feed line names the commit and where the hire sends data (#424)',()=>{
  const office=State.createOfficeState({now:()=>100});
  office.apply({type:'recruit.done',request_id:'r1',data:{slug:'scanpy',agent:hire,passed_probation:true}});
  assert.match(office.S.feed[0].text,/입사했어요 \(수습 통과\)/);
  assert.match(office.S.feed[0].text,/commit 0123456/);assert.match(office.S.feed[0].text,/외부 전송 api\.example\.org/);
  office.apply({type:'recruit.done',request_id:'r1',data:{slug:'old',agent:{id:'c_old',name:'Old'},passed_probation:false}});
  assert.equal(office.S.feed[0].text,'Old 입사했어요 (수습 진행 중)','an older runner without provenance keeps the old line');
  office.apply({type:'contract.warning',agent_id:'c_tissue',data:{agent_id:'c_tissue',changed:['skill','mcp']}});
  assert.match(office.S.feed[0].text,/c_tissue.*skill, mcp/);assert.equal(office.S.feed[0].cls,'alert');
});

test('the 채용 제안 card says what hiring records before the PI decides',async()=>{
  const decide=await load('decide.js');
  const container=new Element();
  const [row]=decide.syncDecisionCards(container,[],[{id:'sug_1',repo:'https://github.com/scverse/scanpy',reason:'scRNA 전문가 없음',created_at:1}]);
  const p=row._decisionParts;
  assert.equal(p.consequence.hidden,false);
  assert.match(p.consequence.textContent,/commit·license·외부 전송/);
  assert.match(p.consequence.textContent,/통제 데이터/);
});

test('the fox sheet lists every precedent and a row opens its skill card as text (#660)',async()=>{
  const lit=await load('literature.js');
  const skill={status:'ready',built:true,reason:null,selection_reason:'같은 QC 방법',agents:['analyst'],skill_sha256:'a'.repeat(64),
    paper2agent_commit:COMMIT,selected_steps:['s1'],attached_steps:['s1'],reference:'referenced',reference_reasons:['result_reference']};
  const view={status:'ok',papers:[{title:'<img src=x onerror=alert(1)>',ids:['doi:10.1000/fit','pmid:42'],uses:['planning','paper_skill'],skill,recruit:null},
    {title:'Context only',ids:['javascript:alert(1)'],uses:['planning'],skill:null,recruit:null}]};
  const box=new Element();
  const [first,second]=lit.syncPaperLiterature(box,view);
  assert.equal(box.children[0].textContent,'선행 연구 2편 · 자문 스킬 1 · 재현 채용 0','the count is all papers, not the skills');
  assert.match(first.textContent,/^<img src=x onerror=alert\(1\)> · 계획 참고 · 자문 스킬/,'a title stays text');
  assert.deepEqual(nodes(first).filter(el=>el.tagName==='A').map(el=>el.href),['https://doi.org/10.1000/fit','https://pubmed.ncbi.nlm.nih.gov/42/']);
  assert.match(first.textContent,/skill aaaaaaaaaaaa · Paper2Agent 0123456/);
  assert.match(first.textContent,/결과에서 참조 확인\(도구 호출·효용 증거 아님\)/);
  assert.ok(!nodes(second).some(el=>el.tagName==='A'),'an unknown scheme is never a link');
  assert.match(lit.skillFacts({...skill,built:false,status:'skipped',reason:'자료 요청 필요',reference:'not_dispatched'}).map(r=>r.join(' ')).join('\n'),
    /상태 생략 \(자료 요청 필요\)\n생성 버전 생성 안 됨[\s\S]*결과 참조 단계 실행 전/);
  // #660 stage 2: other requests' uses of the same skill, with the basis of the count; an older gateway sends none and shows no row.
  const facts=(sk,h)=>lit.skillFacts(sk,h).map(r=>r.join(' ')).join('\n');
  const prior={...skill,previous_uses:[{request_id:'req_old',created_at:1700000000,attached_steps:['s2'],agents:['analyst'],reference:'referenced'}],previous_use_count:3};
  assert.match(facts(prior,{limit:500,scanned:480,truncated:false}),/이전 사용 3건 \(최근 480건 기준\): req_old 2023-11-14 · 단계 s2 · 결과에서 참조 확인[^\n]*외 2건/);
  assert.match(facts({...skill,previous_uses:[],previous_use_count:0},{limit:500,scanned:500,truncated:true}),
    /이전 사용 다른 요청에서 고른 기록 없음 \(최근 500건 기준, 더 오래된 요청은 세지 않음\)/);
  assert.ok(!lit.skillFacts(skill).some(row=>row[0]==='이전 사용'),'no row without the gateway sending the history');
  assert.ok(!facts({...prior,previous_uses:[{request_id:'<b>x</b>',created_at:'bad',attached_steps:'no',reference:'weird'}]},null).includes('<script'));
  assert.equal(lit.summaryText({status:'absent',papers:[]}),'이 요청에는 선행 연구 기록이 없어요.');
  const office=State.createOfficeState({now:()=>100});
  office.apply({type:'request.created',request_id:'r1',data:{text:'t'}});
  assert.equal(office.fillRequestDetail('r1',{paper_literature:view}),0,'the paper list is not counted as filled report text');
  assert.equal(office.S.requests.get('r1').paperLiterature,view);
  assert.ok(!Object.keys(office.S.requests.get('r1')).includes('paperLiterature'));
});
