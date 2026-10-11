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
