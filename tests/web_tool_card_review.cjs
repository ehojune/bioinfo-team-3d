const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const path = require('node:path');
const root = path.resolve(__dirname, '../labhq/web');
test('staff activity summarizes scripts and retains the received command', () => {
  const ctx = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(root, 'state.js'), 'utf8'), ctx);
  const office = ctx.LabHQState.createOfficeState();
  office.apply({type:'snapshot', data:{agents:[{id:'recruiter'}]}});
  const raw = 'P=.tmp/p2s-scripts/paper_bundle.py uv run "$P" build --work .tmp/paper-review';
  office.apply({type:'agent.tool', agent_id:'recruiter', data:{name:'Bash', input:raw, input_truncated:true, input_limit:400}});
  const row = office.ag('recruiter').log.at(-1);
  assert.equal(row.piText, '논문 묶음을 만드는 스크립트 실행(uv)');
  assert.ok(row.fullText.includes(raw));
  assert.equal(office.toolActivitySummary({name:'Bash', input:raw}), row.piText);
  assert.ok(!Object.keys(row).includes('piText'), 'display copy must not change replay state');
  assert.equal(office.toolActivitySummary({name:'Bash', input:'python opaque.py'}), '스크립트 실행(python)');
});
test('PI detail toggles hide internal truncation captions', async () => {
  class Element {
    constructor(tag) { this.tagName=tag; this.children=[]; this.textContent=''; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute() {}
  }
  global.document={createElement:tag=>new Element(tag)};
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {expandableText}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const parent=new Element('div');
  expandableText(parent, 'uv run script.py', {fold:true,summary:'자세히',truncated:true,cutChars:400});
  const texts=n=>[n.textContent,...n.children.flatMap(texts)].join(' ');
  assert.ok(!texts(parent).includes('서버에서'));
  assert.ok(texts(parent).includes('uv run script.py'));
});

test('approval totals discard stale results and do not invent zero on failure', async () => {
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {createApprovalSummaryLoader,grantCategoryLabel}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  let current='one'; const pending=[];
  const loader=createApprovalSummaryLoader(id=>new Promise((resolve,reject)=>pending.push({id,resolve,reject})),()=>current,()=>{});
  const first=loader.refresh(); current='two'; const second=loader.refresh();
  pending[1].resolve({request_id:'two',automatic_approved_count:203,granted_categories:['workspace'],active_grant_categories:[]});
  await second;
  pending[0].resolve({request_id:'one',automatic_approved_count:1,granted_categories:[],active_grant_categories:[]});
  await first;
  assert.equal(loader.get().automatic_approved_count,203);
  assert.equal(grantCategoryLabel('workspace'),'작업 폴더');
  assert.equal(grantCategoryLabel('future_internal_key'),'알 수 없는 부류');
  const third=loader.refresh(); pending[2].reject(new Error('offline')); await third;
  assert.equal(loader.get(),null);
  const fourth=loader.refresh(); pending[3].resolve({request_id:'two',automatic_approved_count:0}); await fourth;
  assert.equal(loader.get(),null);
});

test('slow approval summary polls are serialized and a queued refresh catches up', async () => {
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {createApprovalSummaryLoader}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const pending=[];
  const loader=createApprovalSummaryLoader(id=>new Promise(resolve=>pending.push({id,resolve})),()=> 'one',()=>{});
  const first=loader.refresh();
  await loader.refresh(); await loader.refresh();
  assert.equal(pending.length,1);
  pending[0].resolve({request_id:'one',automatic_approved_count:1,granted_categories:[],active_grant_categories:[]});
  await first;
  assert.equal(loader.get().automatic_approved_count,1);
  assert.equal(pending.length,2);
  pending[1].resolve({request_id:'one',automatic_approved_count:2,granted_categories:[],active_grant_categories:[]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(loader.get().automatic_approved_count,2);
});

test('approval summary tolerates no request or a stale response', async () => {
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {approvalSummaryText}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const summary={request_id:'r1',automatic_approved_count:3,granted_categories:['workspace'],active_grant_categories:[]};
  assert.equal(approvalSummaryText(null,null),'집계 확인 불가');
  assert.equal(approvalSummaryText({id:'other'},summary),'집계 확인 불가');
  assert.match(approvalSummaryText({id:'r1'},summary),/자동 승인 3건 · 허용한 부류: 작업 폴더 · 현재 유효: 없음/);
});

test('only recognized server grant categories on tool cards expose eligibility', async () => {
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {grantCategory}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const card={kind:'tool_permission',request_id:'r',detail:{grant_category:'workspace'}};
  assert.equal(grantCategory(card),'workspace');
  assert.equal(grantCategory({...card,request_id:null}),null);
  assert.equal(grantCategory({...card,kind:'budget'}),null);
  assert.equal(grantCategory({...card,detail:{grant_category:'unknown'}}),null);
});

test('tool reasons are deterministic and grant categories do not inherit high risk', async () => {
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {toolCardReason,decisionFacts}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  for (const [category, expected] of [['workspace','작업 폴더'],['unresolved_path','경로를 미리 확인하지 못했습니다'],['internal_read','내부 자료']]) {
    const card={kind:'tool_permission',detail:{grant_category:category,tool_name:'Bash',policy_reason:'Bash read scope is ambiguous (policy.private_paths)'},pi_reason:'위험 등급: 높음 · 개인 정보'};
    assert.ok(toolCardReason(card).includes(expected));
    if (category !== 'internal_read') assert.ok(!decisionFacts(card).some(([label])=>label==='위험 등급'));
  }
  for (const [reason, text] of [['restricted zone','닫힌 경로'],['PI personal path (policy.private_paths)','다른 개인 경로'],['recursive delete','삭제'],['network access','네트워크'],['write outside workspace','작업 폴더 밖'],['scheduler submit','스케줄러']])
    assert.ok(toolCardReason({detail:{policy_reason:reason}}).includes(text),reason);
  assert.ok(decisionFacts({kind:'tool_permission',detail:{policy_reason:'restricted zone'},pi_reason:'위험 등급: 높음 · 확인'}).some(([label,value])=>label==='위험 등급' && value==='높음'));
  assert.ok(!decisionFacts({kind:'tool_permission',detail:{},pi_reason:'위험 등급: 높음 · 확인'}).some(([label])=>label==='위험 등급'));
  const destructive={kind:'tool_permission',detail:{grant_category:'unresolved_path',tool_input:{command:'cd outputs && rm -r data'},policy_reason:'Bash read scope is ambiguous (policy.private_paths)'},pi_reason:'위험 등급: 높음 · 삭제'};
  assert.match(toolCardReason(destructive),/삭제/);
  assert.ok(decisionFacts(destructive).some(([label,value])=>label==='위험 등급' && value==='높음'));
  const mixed={...destructive,detail:{...destructive.detail,policy_reason:'Bash reaches a closed or credential path'}};
  assert.match(toolCardReason(mixed),/닫힌 경로/); assert.match(toolCardReason(mixed),/삭제/);
  const compound={...destructive,detail:{...destructive.detail,tool_input:{command:'curl https://example.com && rm -r data; sbatch job.sh'}}};
  for (const reason of ['삭제','네트워크','스케줄러']) assert.ok(toolCardReason(compound).includes(reason));
  const quoted={...destructive,detail:{...destructive.detail,tool_input:{command:"printf 'rm -r data'"}}};
  assert.match(toolCardReason(quoted),/경로를 미리 확인하지 못했습니다/);
  assert.ok(!decisionFacts(quoted).some(([label])=>label==='위험 등급'));
  for (const [command,reason] of [['curl https://example.com','네트워크'],['sbatch job.sh','스케줄러'],['mv a b','작업 폴더'],['sed -i s/a/b/ file','작업 폴더']]) {
    assert.ok(toolCardReason({detail:{grant_category:'workspace',tool_input:{command}}}).includes(reason),command);
  }
});

test('review cards show the corresponding count including zero and hide unavailable totals', async () => {
  class Element {
    constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this._text=''; }
    set textContent(v) { this._text=String(v); this.children=[]; }
    get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children=nodes; this._text=''; }
    setAttribute() {}
  }
  global.document={createElement:tag=>new Element(tag)};
  global.LabHQState={needsPiReview:()=>true,needsStorage:()=>false,reviewLabel:()=> '검토 대기'};
  const source=fs.readFileSync(path.join(root,'ui/decide.js'),'utf8');
  const {syncReviewCards}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const parent=new Element('div'), requests=[{id:'a',status:'done'},{id:'b',status:'done'},{id:'c',status:'done'}];
  const summaries=new Map([['a',{request_id:'a',automatic_approved_count:0}],['b',{request_id:'b',automatic_approved_count:4}],['c',{request_id:'other',automatic_approved_count:5}]]);
  const rows=syncReviewCards(parent,requests,{presentation:'journal',approvalSummaries:summaries});
  assert.match(rows[0].textContent,/자동 승인0건/); assert.match(rows[1].textContent,/자동 승인4건/);
  assert.doesNotMatch(rows[2].textContent,/자동 승인/);
});
