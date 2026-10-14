const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
require('../labhq/web/state.js');
const state = globalThis.LabHQState;
const source = fs.readFileSync(require('node:path').join(__dirname, '../labhq/web/ui/decide.js'), 'utf8');
const decide = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
class Element {
  constructor(tag='div') {this.tagName=tag;this.children=[];this.dataset={};this.value='';this._text='';}
  set textContent(v) {this._text=String(v);this.children=[];}
  get textContent() {return this._text+this.children.map(c=>c.textContent).join('');}
  append(...c) {this.children.push(...c);}
  replaceChildren(...c) {this._text='';this.children=c;}
}
global.document={createElement:tag=>new Element(tag)};
test('terminal report waits for PI review, emits one toast, and reviewed replay clears it', () => {
  const office=state.createOfficeState();
  const ev={type:'request.completed',request_id:'r',data:{ok:true,report:'Report',pi_review_pending:true}};
  const first=office.apply(ev);
  assert.equal(state.requestStatusText(office.req('r')), '보고서 검토 대기');
  assert.equal(first.filter(e=>e.type==='toast').length,1);
  assert.equal(office.apply(ev).filter(e=>e.type==='toast').length,0);
  office.apply({type:'request.reviewed',request_id:'r',data:{pi_reviewed_at:100}});
  assert.equal(state.requestStatusText(office.req('r')), '완료');
});
test('unreviewed report wins automatic focus over newer reviewed result', () => {
  const office=state.createOfficeState();
  office.apply({type:'snapshot',data:{requests:[{id:'r',status:'done',pi_review_pending:true,created_at:1},
    {id:'new',status:'done',pi_reviewed_at:10,created_at:10}]}});
  assert.equal(office.S.current,'r');
});
test('ask waiting and stale waiting do not claim a PI decision', () => {
  const office=state.createOfficeState();
  office.apply({type:'snapshot',data:{agents:[{id:'sci_reviewer',state:'waiting'}]}});
  const a=office.ag('sci_reviewer');
  assert.notEqual(office.visual(a),'waiting');
  office.apply({type:'agent.status',agent_id:a.id,data:{state:'waiting',ask:'a'}});
  assert.equal(office.stateText(a),'답 기다리는 중');
});
test('staff names and artwork work before roster and normalize id case', () => {
  const office=state.createOfficeState();
  assert.equal(office.nick('facilities'),'시설팀');
  assert.equal(office.nick('CSO'),'CSO(부엉이)');
  assert.equal(state.staffCharacter({id:'CSO'}),'owl');
  assert.equal(state.staffCharacter({id:'unknown'}),'neutral');
  assert.equal(state.staffCharacter({id:'c_example'}),'chick');
});
test('existing decision avatar redraws after late roster without replacing inputs', async () => {
  const ui=await decide, container=new Element();let avatar='neutral';
  const options={avatarHTML:()=>avatar}, a={id:'a',agent_id:'CSO',kind:'budget'};
  const [row]=ui.syncDecisionCards(container,[a],[],options);
  row._decisionParts.note.value='Keep my answer'; avatar='owl';
  const [next]=ui.syncDecisionCards(container,[a],[],options);
  assert.equal(next,row);assert.equal(row.children[0].innerHTML,'owl');
  assert.equal(row._decisionParts.note.value,'Keep my answer');
});
test('review cards have open and explicit finish actions without deadline', async () => {
  const ui=await decide, container=new Element();let opened='', reviewed='';
  const rows=ui.syncReviewCards(container,[{id:'r',text:'Request',status:'done',pi_review_pending:true}],
    {onOpen:id=>opened=id,onDone:id=>reviewed=id});
  const nodes=el=>[el,...el.children.flatMap(nodes)];
  const buttons=nodes(rows[0]).filter(c=>c.tagName==='button');
  assert.match(rows[0].textContent,/보고서 검토/);assert.doesNotMatch(rows[0].textContent,/자동 승인|기한/);
  buttons[0].onclick();buttons[1].onclick();assert.equal(opened,'r');assert.equal(reviewed,'r');
});
test('unknown cost uses the same readable noun phrase', () => {
  assert.equal(state.totalCostLabel([{costKnown:false}],25.36),'사용 비용 $25.36 + 미집계된 비용');
  assert.match(state.costLabel({costSummary:{actual_usd:25.36,unknown_count:1}}),/미집계된 비용 1건/);
});
test('budget card explains unknown costs and both decisions in sentences', async () => {
  const ui=await decide;
  const text=ui.displaySummary({kind:'budget',summary:'Old summary',detail:{spent_usd:25.36,
    limit_usd:30,unknown_count:1,unknown_reserve_usd:10,requested_budget_usd:60}});
  assert.match(text,/지금까지 \$25.36 썼고/);
  assert.match(text,/비용을 아직 모르는 작업이 1건/);
  assert.match(text,/상한 \$30.00/);
  assert.match(text,/승인하면 상한을 \$60.00로 올리고, 거절하면 새 작업을 보내지 않습니다/);
});
test('whiteboard exposes completed report action and staff keep completion until review', () => {
  const office=state.createOfficeState();
  office.apply({type:'snapshot',data:{agents:[{id:'cso'}],requests:[{id:'r',text:'Study',status:'done',pi_review_pending:true}]}});
  const html=fs.readFileSync(require('node:path').join(__dirname,'../labhq/web/index.html'),'utf8');
  const board={dataset:{},innerHTML:''};
  const vm=require('node:vm'), ctx=vm.createContext({S:office.S,LabHQState:state,$:()=>board,
    esc:String,short:String,processingLabel:()=>'',phaseList:()=>'',nick:office.nick});
  vm.runInContext(html.slice(html.indexOf('function renderBoard()'),html.indexOf('function renderRack()'))+'\nrenderBoard();',ctx);
  assert.doesNotMatch(board.innerHTML,/Study/,'request title belongs only in the request list');assert.match(board.innerHTML,/✓ 보고서 검토 대기/);
  assert.match(board.innerHTML,/data-open-review="r"[^>]*>보고서 열기/);
  assert.equal(office.visual(office.ag('cso')),'done');
  office.apply({type:'request.reviewed',request_id:'r',data:{pi_reviewed_at:100}});
  assert.equal(office.visual(office.ag('cso')),'idle');
});
test('snapshot review state wins over old terminal replay and failure uses acknowledgment', () => {
  const office=state.createOfficeState();
  const ev={type:'request.failed',request_id:'r',data:{error:'Reason',pi_review_pending:true}};
  office.apply(ev);
  assert.equal(state.requestStatusText(office.req('r')),'결과 확인 대기');
  const effects=office.apply({type:'snapshot',data:{requests:[{id:'r',status:'failed',pi_review_pending:false,pi_reviewed_at:100}],recent_events:[ev]}});
  assert.equal(state.needsPiReview(office.req('r')),false);
  assert.equal(effects.filter(e=>e.type==='toast').length,0);
});

test('CP4 uses one report card, binds both choices and replays acceptance', async () => {
  const office=state.createOfficeState(), final={revision:1,target_sha256:'a'.repeat(64),deadline_at:200};
  office.apply({type:'request.completed',request_id:'r',data:{ok:true,status:'review_pending',
    report:'Final draft',pi_review_pending:true,final_checkpoint:final}});
  assert.equal(office.req('r').status,'review_pending');
  assert.equal(state.requestStatusText(office.req('r')),'보고서 검토 대기');
  const ui=await decide, container=new Element(), calls=[];
  const rows=ui.syncReviewCards(container,[office.req('r')],{onDone:(id,target)=>calls.push([id,target])});
  assert.equal(rows.length,1);
  const nodes=el=>[el,...el.children.flatMap(nodes)], buttons=nodes(rows[0]).filter(c=>c.tagName==='button');
  assert.deepEqual(buttons.map(b=>b.textContent),['보고서 열기','검토 완료','수용 거부']);
  buttons[1].onclick();buttons[2].onclick();
  assert.deepEqual(calls.map(c=>c[1]),[
    {decision:'accepted',revision:1,target_sha256:final.target_sha256},
    {decision:'rejected',revision:1,target_sha256:final.target_sha256}]);
  office.apply({type:'request.reviewed',request_id:'r',data:{status:'done',pi_reviewed_at:100,
    final_checkpoint:{...final,receipt:{decision:'accepted'}}}});
  assert.equal(state.needsPiReview(office.req('r')),false);
  assert.equal(office.req('r').status,'done');
});
