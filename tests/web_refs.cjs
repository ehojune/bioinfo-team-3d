const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('reference chips must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const load=name=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(web,name),'utf8')).toString('base64'));
const nodes=el=>[el,...el.children.flatMap(nodes)];

test('the chip input infers the same kinds as labhq send --ref',async()=>{
  const {inferReference}=await load('ui/refs.js');
  const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/reference_inference.json'),'utf8'));
  for (const [text,expected] of cases) assert.deepEqual(inferReference(text),expected,text);
});

test('chips render as text with remove buttons and a default-reference toggle',async()=>{
  const {syncReferenceChips}=await load('ui/refs.js');
  const container=new Element(),removed=[],toggles=[];
  const items=[{kind:'github',value:'<b>owner</b>/repo'},{kind:'path',value:'/srv/refs/notes'}];
  syncReferenceChips(container,{items,useDefaults:true},[{kind:'pmid',value:'1',note:'lab default'}],
    {onRemove:i=>removed.push(i),onToggleDefaults:()=>toggles.push(true)});
  assert.match(container.textContent,/GitHub.*<b>owner<\/b>\/repo/);
  assert.match(container.textContent,/경로.*\/srv\/refs\/notes/);
  const buttons=nodes(container).filter(n=>n.tagName==='BUTTON');
  const toggle=buttons.find(b=>b.dataset.refDefaults);
  assert.equal(toggle.ariaPressed,'true');assert.match(toggle.textContent,/기본 참고 1건/);
  buttons.filter(b=>b.dataset.refRemove!==undefined)[1].onclick();
  toggle.onclick();
  assert.deepEqual(removed,[1]);assert.equal(toggles.length,1);
  syncReferenceChips(container,{items:[],useDefaults:false},[],{});
  assert.equal(nodes(container).filter(n=>n.tagName==='BUTTON').length,0,'no toggle without PI defaults');
});

test('the shared reducer keeps request pointers and the PI defaults',()=>{
  require(path.join(web,'state.js'));
  const office=globalThis.LabHQState.createOfficeState({now:()=>10});
  const refs=[{kind:'doi',value:'10.1038/nature12373',note:null,source:'request'}];
  office.apply({type:'snapshot',data:{agents:[],requests:[{id:'r1',text:'t',status:'done',references:refs}],
    default_references:[{kind:'github',value:'https://github.com/lab/protocols'}]}});
  assert.deepEqual(office.S.requests.get('r1').references,refs);
  assert.equal(office.S.defaultRefs.length,1);
  office.apply({type:'request.created',request_id:'r2',data:{text:'u',mode:'orchestrate',references:refs}});
  assert.deepEqual(office.S.requests.get('r2').references,refs);
  office.apply({type:'snapshot',data:{agents:[],requests:[]}});
  assert.deepEqual(office.S.defaultRefs,[],'a new snapshot replaces the defaults');
});

test('the 2.5D composer sends pointers with the request',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  assert.match(html,/from '\.\/ui\/refs\.js'/);
  assert.match(html,/id="ref-input"/);
  assert.match(html,/references: refs\.items\.map\(\(\{kind, value\}\) => \(\{kind, value\}\)\)/);
  assert.match(html,/default_references: refs\.useDefaults/);
});
