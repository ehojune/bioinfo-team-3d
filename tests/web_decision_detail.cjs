const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {test} = require('node:test');

// Native textContent semantics; any HTML insertion is a test failure.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('approval detail must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const source=fs.readFileSync(path.join(__dirname,'../labhq/web/ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const nodes=el=>[el,...el.children.flatMap(nodes)];
async function card(kind,detail) {
  const decide=await modulePromise,container=new Element();
  const approval={id:'example',kind,detail};
  const [row]=decide.syncDecisionCards(container,[approval]);
  return {decide,container,approval,row,detail:row._decisionParts.detail};
}

test('tool_permission command, arguments and all extra fields appear as literal DOM text',async()=>{
  for (const input of [{command:'printf "<img src=x onerror=alert(1)>"',args:['--example','a&b']},
    JSON.stringify({command:'printf "<img src=x onerror=alert(1)>"',args:['--example','a&b']})]) {
    const c=await card('tool_permission',{tool_name:'Bash',input,extra:'<script>example</script>'});
    assert.match(c.detail.textContent,/printf/,'command must be visible before approval');
    assert.match(c.detail.textContent,/tool_name.*Bash/s);
    const pre=nodes(c.detail).find(n=>n.tagName==='PRE'&&n.textContent.includes('command'));
    assert.equal(pre.textContent,JSON.stringify(typeof input==='string'?JSON.parse(input):input,null,2));
    assert.match(c.detail.textContent,/<script>example<\/script>/);
    assert.equal(nodes(c.detail).some(n=>['IMG','SCRIPT'].includes(n.tagName)),false);
  }
});

test('hpc_submit shows queue, script path, full script preview and every resource',async()=>{
  const detail={reason:'example',queue:'example.q',script_path:'jobs/example.sh',
    script_preview:'#!/bin/bash\nprintf "<example>&"\n',cores:4,mem:'8G',walltime:'01:00:00',resources:{gpu:0},extra:false};
  const c=await card('hpc_submit',detail);
  assert.ok(c.detail.textContent.includes(detail.script_preview),'script_preview must be visible before approval');
  for (const [key,value] of Object.entries(detail)) {
    assert.ok(c.detail.textContent.includes(key),key+' label must be visible');
    assert.ok(c.detail.textContent.includes(typeof value==='object'?JSON.stringify(value,null,2):String(value)),key+' value must be visible');
  }
});

test('long values expand in full and stay expanded across keyed updates',async()=>{
  const script='printf example\n'.repeat(90)+'# final line <example>';
  const c=await card('hpc_submit',{script_preview:script});
  const fold=nodes(c.detail).find(n=>n.tagName==='DETAILS');
  assert.ok(fold,'long values need a native expandable control');
  assert.ok(!fold.open,'long value starts collapsed');
  assert.equal(nodes(fold).find(n=>n.tagName==='PRE').textContent,script,'no content is truncated');
  fold.open=true;c.row._decisionParts.note.value='draft';
  c.decide.syncDecisionCards(c.container,[{...c.approval,detail:{script_preview:script}}]);
  assert.ok(nodes(c.detail).includes(fold),'unchanged details retain their DOM');
  assert.equal(fold.open,true);assert.equal(c.row._decisionParts.note.value,'draft');
  c.decide.syncDecisionCards(c.container,[{...c.approval,detail:{script_preview:'replacement'}}]);
  assert.match(c.detail.textContent,/replacement/);assert.ok(!c.detail.textContent.includes('final line'));
});

test('other approval kinds preserve every key/value and legacy detail strings',async()=>{
  const c=await card('future_kind',{'<key>':{nested:[0,false,null]},empty:'',zero:0,no:false,nil:null});
  for (const key of ['<key>','empty','zero','no','nil']) assert.ok(c.detail.textContent.includes(key));
  assert.match(c.detail.textContent,/\n  "nested": \[/);assert.match(c.detail.textContent,/null/);
  const legacy=await card('clarify','Question <example>');assert.match(legacy.detail.textContent,/Question <example>/);
  assert.equal((await card('clarify',null)).detail.hidden,true);
});

test('research plan card shows the full canonical PLAN in readable sections',async()=>{
  const plan={schema_version:2,topics:[],intake:{scope_status:'in_scope'},brief:{question:'Does condition change expression?',
    primary_hypothesis:'Condition changes expression',null_or_alternatives:['batch explains the change'],
    completion_conditions:['QC and effect interval reported']},protocol:{revision:1,
    stop_conditions:['design not identifiable'],resource_limits:['one local planning call'],
    data_boundaries:['public counts only'],packs:[{id:'single_cell_de',version:'1',sha256:'a'.repeat(64)}]},
    pack_values:{'single_cell_de@1':{fields:{donor_id:'metadata.donor_id',batch:'library_batch',count_scale:'raw_counts'},
      validators:{'single_cell_de.donor_unit':'donor-level pseudobulk'},acceptance:{'single_cell_de.donor_model':'accepted'}}},
    pack_applicability:{'bulk_tumor_normal@1':{applied:false,topics_any:['bulk_rna_seq'],matched_topics:[],reason:'topics_empty'}},
    warnings:['topics is empty; topic-conditioned packs were not applied'],
    clarifying_questions:[],steps:[{id:'s1'}],recruit:[],notes:'frozen'};
  // Use Python-compatible recursively sorted canonical JSON for the approval payload.
  const sortValue=value=>Array.isArray(value)?value.map(sortValue):value&&typeof value==='object'?
    Object.fromEntries(Object.keys(value).sort().map(key=>[key,sortValue(value[key])])):value;
  const canonicalPlan=JSON.stringify(sortValue(plan));
  const sha=crypto.createHash('sha256').update(canonicalPlan,'utf8').digest('hex');
  const c=await card('research_plan',{gate:'research_plan',target_sha256:sha,plan_canonical:canonicalPlan});
  assert.equal(crypto.createHash('sha256').update(canonicalPlan,'utf8').digest('hex'),sha);
  for (const label of ['질문','가설','완료 조건','중단 조건','자원 상한','data boundary','topics','pack 적용 판정','경고','pack 값','동결 PLAN 전체'])
    assert.ok(c.detail.textContent.includes(label),label+' must be visible');
  for (const value of ['Does condition change expression?','Condition changes expression','design not identifiable',
    'public counts only','metadata.donor_id','raw_counts']) assert.ok(c.detail.textContent.includes(value),value+' must be visible');
  assert.ok(c.detail.textContent.includes(canonicalPlan),'the exact hash input must remain visible');
});

test('research_continue lists each review P1 issue by step with its problem and requested fix',async()=>{
  const issues=[{step_id:'s3',claim_id:'confounding_assessed',priority:'P1',category:'other',
    evidence_quote:'stage_grp = "I"',problem:'stage IV is coded as stage I <example>',request:'map every stage explicitly'},
    {step_id:'s6',priority:'P1',problem:'pathways are filtered by observed overlap before correction'}];
  const c=await card('research_continue',{gate:'research_continue',plan_sha256:'a'.repeat(64),round:2,limit:2,p1_issues:issues});
  const text=c.detail.textContent;
  for (const value of ['리뷰 P1 지적 (2건)','1. s3 · claim confounding_assessed','문제: stage IV is coded as stage I <example>',
    '고칠 점: map every stage explicitly','2. s6','이어 가기 차수','이어 가기 상한','지난 계획 plan hash'])
    assert.ok(text.includes(value),value+' must be visible');
  assert.ok(text.indexOf('리뷰 P1 지적')<text.indexOf('이어 가기 차수'),'the issues come first');
  assert.ok(!/(^|[^_])gate/.test(text.replace(/research_continue/g,'')),'the internal gate name is not shown');
  assert.ok(text.includes('"evidence_quote"'),'the raw issue JSON stays available');
  assert.equal(nodes(c.detail).some(n=>['IMG','SCRIPT'].includes(n.tagName)),false);
});

test('resume card shows the received time as a date and the steps left as a list, without repeating the request',async()=>{
  const created=new Date(2026,9,8,11,33).getTime()/1000;
  const c=await card('resume',{request_text:'GEO GSE10072 example request',created_at:created,steps:[]});
  const text=c.detail.textContent;
  assert.ok(text.includes('접수 시각2026-10-08 11:33'),text);
  assert.ok(text.includes('남은 단계없음'),text);
  assert.ok(!text.includes('GEO GSE10072 example request'),'the summary already quotes the request');
  assert.ok(!text.includes(String(created)),'no epoch seconds');
  const left=await card('resume',{created_at:created,steps:['s2','s3']});
  assert.ok(left.detail.textContent.includes('남은 단계s2, s3'));
});

test('budget card shows dollar amounts to the cent under Korean names, including the cap 승인 sets',async()=>{
  const c=await card('budget',{spent_usd:25.860455119999997,limit_usd:30,requested_budget_usd:60,unknown_count:1,unknown_reserve_usd:5});
  const text=c.detail.textContent;
  for (const value of ['지금까지 쓴 비용$25.86','지금 상한$30.00','승인하면 새 상한$60.00','비용 미집계 작업1','미집계 작업 1건당 가정 비용$5.00'])
    assert.ok(text.includes(value),value+' must be visible: '+text);
  assert.ok(!text.includes('25.8604'),'no float noise');
});
