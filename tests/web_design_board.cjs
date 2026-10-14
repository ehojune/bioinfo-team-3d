const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const web = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(web, 'ui/components.css'), 'utf8');
const stateContext = vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(web, 'state.js'), 'utf8'), stateContext);
const state = stateContext.LabHQState;
const load = file => import('data:text/javascript;base64,' + fs.readFileSync(path.join(web, 'ui', file)).toString('base64'));
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this._text=''; this.listeners={}; }
  set textContent(v) { this._text=String(v); this.children=[]; }
  get textContent() { return this._text+this.children.map(e=>e.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text=''; this.children=children; }
  setAttribute(k,v) { this.attributes[k]=v; }
  addEventListener(k,v) { this.listeners[k]=v; }
}
global.document={createElement:tag=>new Element(tag)};
const nodes=e=>[e,...e.children.flatMap(nodes)];
const has=(e,c)=>String(e.className||'').split(' ').includes(c);
test('empty columns are hidden and completed steps occupy only a closed summary', async()=>{
  const {syncTaskBoard}=await load('tasks.js'), root=new Element();
  syncTaskBoard(root,{id:'r',plan:[{id:'s1',instruction:'자료 확인',agent_id:'analyst'}],steps:{s1:'done'}},new Map());
  const columns=nodes(root).filter(e=>e.dataset.column);
  assert.equal(columns.filter(e=>e.hidden).length,3);
  const done=columns.find(e=>e.dataset.column==='done');
  assert.equal(done.tagName,'details'); assert.equal(Boolean(done.open),false);
  assert.match(done.children[0].textContent,/완료.*1/);
  assert.match(css,/\.kanban-column\[hidden\]\s*\{\s*display:\s*none/);
  assert.match(css,/\.kanban\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
});
test('task summary includes instruction, Korean staff name and a dot with status text',async()=>{
  const root=new Element();
  (await load('tasks.js')).syncTaskBoard(root,{id:'r',plan:[{id:'s1',instruction:'자료 확인',agent_id:'analyst'}],steps:{s1:'working'}},new Map(),{nick:()=> '분석가'});
  const card=nodes(root).find(e=>has(e,'task-card')), summary=card.children[0];
  assert.match(summary.textContent,/자료 확인/); assert.match(summary.textContent,/분석가/);
  assert.match(summary.textContent,/진행 중/); assert.ok(nodes(summary).some(e=>has(e,'status-dot')));
});
test('request title occurs once in the full-title recent list, with review status and accessible selection',()=>{
  const title='긴 요청 제목을 중간에서 자르지 않고 끝까지 읽으며 지난 연구 결과를 확인합니다';
  const all=[{id:'r',text:title,status:'done',pi_review_pending:true},{id:'s',text:'다른 요청',status:'running'}];
  const ctx=vm.createContext({all,q:all[0],LabHQState:state,S:{approvals:new Map()},esc:String,short:s=>s.slice(0,40)});
  const start=html.indexOf('  const pick ='),end=html.indexOf('  const proj =',start);
  vm.runInContext(html.slice(start,end)+';globalThis.pick=pick;',ctx);
  assert.doesNotMatch(ctx.pick,/<select/); assert.match(ctx.pick,/<button/);
  assert.ok(ctx.pick.includes(title)); assert.match(ctx.pick,/보고서 검토 대기/);
  assert.match(ctx.pick,/aria-current="true"/); assert.match(ctx.pick,/data-request-id="s"/);
  const board=html.slice(html.indexOf('function renderBoard()'),html.indexOf('function renderRack()'));
  assert.doesNotMatch(board,/short\(q.text|esc\(q.text/);
  const request=html.slice(html.indexOf('function renderRequest()'),html.indexOf('function renderStaff()'));
  assert.doesNotMatch(request,/class="req-text"/);
  assert.equal((board+request).match(/\$\{phaseList\(q\)\}/g)?.length,1);
  assert.match(html,/officeState.selectRequest\(.*dataset.requestId\)/);
});
test('message component keeps Korean names, plain preview and a working details disclosure',async()=>{
  const {syncMessageRows}=await load('messenger.js'), {expandableText}=await load('decide.js');
  const office=state.createOfficeState(), root=new Element();
  const item={who:'analyst',ts:1,text:'## 결과\n**확인했습니다**\n```sh\npython check.py\n```'};
  const options={nick:office.nick,avatarHTML:()=>'',ago:()=> '방금',expandableText};
  const [row]=syncMessageRows(root,[item],options);
  assert.match(row._messageParts.name.textContent,/분석가/);
  const prose=nodes(row).find(e=>has(e,'pi-prose'));
  assert.doesNotMatch(prose.textContent,/[#*`]|python/);
  const details=nodes(row).find(e=>e.tagName==='details');
  assert.equal(details.children[0].textContent,'자세히'); assert.equal(details.open,false);
  details.open=true; details.ontoggle();
  assert.equal(details.children[0].attributes['aria-expanded'],'true');
  assert.match(details.textContent,/python check.py/);
  assert.equal(syncMessageRows(root,[item],options)[0],row); assert.equal(details.open,true);
});
test('staff card includes existing artwork, role, employment, task and dot status in strip and sheet',async()=>{
  const {syncStaffCards}=await load('strip.js'), root=new Element();
  const staff=[{id:'analyst',name:'분석가',role:'자료 분석',state:'working'},{id:'c_one',name:'파견 직원',employment:'contract'}];
  const cards=syncStaffCards(root,staff,{avatarHTML:()=>'<svg></svg>',currentTask:()=> '자료 확인'});
  for(const card of cards) for(const name of ['staff-avatar','staff-role','staff-employment','staff-current','status-dot'])
    assert.ok(nodes(card).some(e=>has(e,name)),name);
  assert.match(cards[0].textContent,/정규직/); assert.match(cards[1].textContent,/파견직/);
  assert.match(cards[0].textContent,/자료 확인/); assert.match(html,/syncStaffCards\(\$\('#sheet-staff'\)/);
});
test('composer primary is 48px and secondary options are disclosed',()=>{
  assert.match(html,/class="[^"]*composer-primary[^"]*" id="send"/);
  assert.match(css,/\.composer-primary[^}]*min-height:\s*var\(--ds-h-touch\)/);
  assert.match(html,/<details[^>]*class="composer-options"/);
  assert.match(html,/refs.items.length \? `첨부·참고 \$\{refs.items.length\}` : '첨부·참고'/);
  assert.match(css,/\.request-choice[^}]*min-height:\s*var\(--ds-h-touch\)/);
});
test('board, message, staff and input text token pairs meet AA in both themes',()=>{
  const tokens=fs.readFileSync(path.join(web,'ui/tokens.css'),'utf8');
  const palette=Object.fromEntries([...tokens.matchAll(/--ds-([\w-]+):\s*light-dark\((#[\da-f]{6}),\s*(#[\da-f]{6})\)/gi)].map(([,k,a,b])=>[k,[a,b]]));
  const luminance=hex=>hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
  const pairs=[['ink','surface'],['ink','sunk'],['ink-2','surface'],['ink-3','surface'],['ink-3','sunk'],['on-accent','accent'],['accent','surface'],['work','surface'],['warn','surface'],['err','surface']];
  for(const theme of [0,1])for(const [fg,bg]of pairs){const a=luminance(palette[fg][theme]),b=luminance(palette[bg][theme]);assert.ok((Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5,`${fg}/${bg}, theme ${theme}`);}
});
