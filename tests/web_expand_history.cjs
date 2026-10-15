const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const root = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase(); this.children=[]; this.dataset={}; this.attributes={}; this._text=''; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_) { throw new Error('received text must remain literal'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text=''; this.children=[]; this.append(...children); }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  getAttribute(key) { return this.attributes[key]; }
}
global.document={createElement:tag=>new Element(tag)};
const nodes=el=>[el,...el.children.flatMap(nodes)];
const find=(el,tag)=>nodes(el).find(n=>n.tagName===tag.toUpperCase());
const source=fs.readFileSync(path.join(root,'ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const storage=()=>{ const data=new Map(); return {getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)}; };

test('received commands, multiline trace and messenger preserve their exact tails across replay and roster updates',()=>{
  const context=vm.createContext({}); vm.runInContext(fs.readFileSync(path.join(root,'state.js'),'utf8'),context);
  const raw='"C:\\WINDOWS\\powershell.exe" -Command "'+('x '.repeat(500))+'<script>tail</script>"\nnext line';
  for (const replay of [false,true]) {
    const office=context.LabHQState.createOfficeState();
    const events=[{type:'roster.updated',data:{agents:[{id:'worker'}]}},
      {type:'agent.tool',agent_id:'worker',ts:10,data:{name:'Bash',input:raw}},
      {type:'agent.log',agent_id:'worker',ts:11,data:{level:'thinking',text:raw}},
      {type:'agent.log',agent_id:'worker',ts:12,data:{text:raw,text_truncated:true}},
      {type:'agent.ask',agent_id:'worker',ts:13,data:{id:'ask',question:raw}},
      {type:'agent.answer',agent_id:'cso',ts:14,data:{ask_id:'ask',answer:raw}}];
    if (replay) office.apply({type:'snapshot',data:{recent_events:events}}); else events.forEach(office.apply);
    let worker=office.S.agents.get('worker');
    assert.ok(worker.log[0].fullText.endsWith(raw)); assert.ok(worker.log[0].text.length<raw.length);
    assert.equal(worker.trace[0].fullText,raw); assert.equal(worker.log[1].truncated,true);
    assert.equal(worker.log[1].cutChars,[...raw].length);
    assert.ok(office.S.feed.every(row=>row.fullText.includes(raw)));
    office.apply({type:'roster.updated',data:{agents:[{id:'worker',name:'Renamed'}]}});
    worker=office.S.agents.get('worker'); assert.equal(worker.trace[0].fullText,raw);
    assert.equal(worker.log[1].fullText,raw);
    assert.equal(Object.keys(worker.log[1]).includes('fullText'),false,'display-only original preserves replay comparisons');
  }
});

test('one native control folds literal text, exposes aria-expanded, marks server cuts and survives refresh',async()=>{
  const {syncExpandableText}=await modulePromise, parent=new Element();
  const raw='<img src=x onerror=bad>\n'+('long '.repeat(150))+'TAIL';
  syncExpandableText(parent,raw,{limit:80,truncated:true});
  const fold=find(parent,'details'),summary=find(fold,'summary');
  assert.equal(fold.open,false); assert.equal(summary.getAttribute('aria-expanded'),'false');
  assert.ok(summary.textContent.includes('…')); assert.match(summary.textContent,/받은 내용 일부/);
  assert.equal(find(fold,'pre').textContent,raw); assert.equal(find(parent,'img'),undefined);
  fold.open=true;fold.ontoggle();assert.equal(summary.getAttribute('aria-expanded'),'true');
  syncExpandableText(parent,raw,{limit:80,truncated:true});assert.equal(find(parent,'details'),fold);
  fold.open=false;fold.ontoggle();assert.equal(summary.getAttribute('aria-expanded'),'false');
  syncExpandableText(parent,'short');assert.equal(find(parent,'details'),undefined);
  assert.equal(parent.textContent,'short');
});

const decision=(id,rid='r1')=>({approval:{id,request_id:rid,kind:'budget',summary:'summary '+id},approved:true,
  decided_at:new Date(2026,9,13,14,5).getTime()/1000});
test('each past decision has its local time, conditional report link, and a persistent view-only hide control',async()=>{
  const {syncDecisionHistory,createHistoryVisibility}=await modulePromise, saved=storage();
  const items=[decision('a'),decision('b','r2')],before=JSON.stringify(items);
  const requests=new Map([['r1',{text:'request one',report:'Report'}],['r2',{text:'request two'}]]);
  const visibility=createHistoryVisibility('live:one',saved),container=new Element();let opened;
  const options={requests,visibility,onOpenRequest:id=>opened=id};
  syncDecisionHistory(container,items,options);
  const rows=nodes(container).filter(n=>n.className==='decision-history-item');
  for (const row of rows) { assert.equal(find(row,'time').textContent,'10-13 14:05'); assert.equal(find(row,'time').dateTime,new Date(items[0].decided_at*1000).toISOString()); }
  const link=find(rows[0],'a');assert.ok(link);assert.equal(find(rows[1],'a'),undefined);
  link.onclick({preventDefault(){}});assert.equal(opened,'r1');
  const hide=find(rows[0],'button');assert.equal(hide.getAttribute('aria-label'),'이 결정 목록에서 숨기기');hide.onclick();
  assert.equal(nodes(container).filter(n=>n.className==='decision-history-item').length,1);
  assert.equal(JSON.stringify(items),before,'gateway decision records are never mutated');
  const refreshed=new Element();syncDecisionHistory(refreshed,items,{requests,visibility:createHistoryVisibility('live:one',saved)});
  assert.equal(nodes(refreshed).filter(n=>n.className==='decision-history-item').length,1);
  for (const scope of ['demo:one','live:two']) assert.equal(createHistoryVisibility(scope,saved).has(items[0]),false);
});

test('storage failure still hides locally; pending cards have no x; paging counts only visible decisions',async()=>{
  const {syncDecisionHistory,createHistoryVisibility,syncDecisionCards}=await modulePromise;
  const denied={getItem(){throw Error('denied')},setItem(){throw Error('full')}};
  const visibility=createHistoryVisibility('live',denied), items=Array.from({length:12},(_,i)=>decision('a'+i));
  const container=new Element();syncDecisionHistory(container,items,{visibility});
  find(container,'button').onclick();assert.equal(visibility.has(items[0]),true);
  let more=nodes(container).find(n=>n.className==='btn decision-history-more');assert.match(more.textContent,/1건/);
  more.onclick();assert.equal(nodes(container).filter(n=>n.className==='decision-history-item').length,11);
  const pending=new Element();syncDecisionHistory(pending,[{...decision('pending'),state:'pending'}],{visibility});
  assert.equal(find(pending,'button'),undefined);
  const active=new Element();syncDecisionCards(active,[{id:'open',kind:'budget',summary:'Waiting'}]);
  assert.equal(nodes(active).some(n=>n.className==='decision-hide'),false);
});

test('decision detail uses the shared fold and preserves an open history line across identical updates',async()=>{
  const {syncDecisionHistory,syncDecisionCards,createHistoryVisibility}=await modulePromise;
  const raw='line\n'.repeat(200)+'TAIL'; const cards=new Element();
  syncDecisionCards(cards,[{id:'open',kind:'tool_permission',detail:{input:raw,input_truncated:true}}]);
  const detailFold=nodes(cards).find(n=>n.className==='expandable-text');assert.ok(detailFold);
  assert.equal(find(detailFold,'pre').textContent,raw);assert.match(find(detailFold,'summary').textContent,/받은 내용 일부/);
  const container=new Element(),items=[{...decision('past'),note:raw}],options={visibility:createHistoryVisibility('live',storage())};
  syncDecisionHistory(container,items,options);const fold=find(container,'details');fold.open=true;
  syncDecisionHistory(container,items,options);assert.equal(find(container,'details'),fold);assert.equal(fold.open,true);
});

test('both offices wire report navigation and share history visibility; 2.5D sheets and messenger use received text',()=>{
  const html=fs.readFileSync(path.join(root,'index.html'),'utf8'),live=fs.readFileSync(path.join(root,'lab3d/src/live.js'),'utf8');
  assert.match(html,/createHistoryVisibility\(`\$\{MODE\}:\$\{INSTANCE\}`\)/);
  assert.match(html,/onOpenRequest:[\s\S]*?officeState.selectRequest\(id\)[\s\S]*?showTab\('tasks'\)/);
  assert.match(html,/expandableText\(row, l.text, \{pi:true, original:l.fullText \?\? l.text/);
  assert.match(html,/syncMessageRows\(\$\('#feed'\), items, \{nick, avatarHTML:avatar, ago, expandableText\}\)/);
  assert.match(fs.readFileSync(path.join(root,'ui/messenger.js'),'utf8'),/options.expandableText\(text, m.fullText \?\? m.text/);
  assert.match(live,/createHistoryVisibility\(`live:/);assert.match(live,/syncDecisionHistory/);
  assert.match(live,/\/api\/approvals\/history\?limit=50/);assert.match(live,/onOpenRequest/);
  assert.match(fs.readFileSync(path.join(root,'lab3d/index.html'),'utf8'),/id="approval-history"/);
});
