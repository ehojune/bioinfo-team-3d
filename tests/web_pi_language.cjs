const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const load = file => import('data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(root,file),'utf8')).toString('base64'));
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.attributes={}; this.dataset={}; this._text=''; }
  set textContent(v) { this._text=String(v); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text=''; this.children=children; }
  setAttribute(k,v) { this.attributes[k]=v; }
}
global.document={createElement:tag=>new Element(tag)};
const nodes=e=>[e,...e.children.flatMap(nodes)];
const ev={seq:1,type:'agent.ask',data:{to:'colleague:analyst',question:'Please compare the tables.'}};
const translated={status:'translated',text:'표를 비교해 차이를 확인하려 합니다.',by:'chief_of_staff'};
test('live translation replaces a row, preserves sender and original; history restores it',async()=>{
  const {createMessenger,messageFromEvent}=await load('ui/messenger.js');
  const h=createMessenger({fetchPage:async()=>({events:[],has_more:false})});
  h.ingest(ev); h.ingest({type:'messenger.translated',data:{message_seq:1,pi_display:translated}});
  assert.equal(h.items.length,1);
  assert.equal(h.items[0].piDisplay.text,translated.text);
  assert.equal(h.items[0].original,'질문: Please compare the tables.');
  assert.equal(h.items[0].to,'analyst');
  assert.deepEqual(messageFromEvent({...ev,data:{...ev.data,pi_display:translated}}),h.items[0]);
});
test('translated prose is visible and exact raw code is under a closed disclosure',async()=>{
  const {syncMessageRows,messageFromEvent}=await load('ui/messenger.js');
  const {expandableText}=await load('ui/decide.js');
  const source='```python\nprint("keep exactly")\n```';
  const m=messageFromEvent({seq:1,type:'agent.log',data:{text:source,pi_display:translated}});
  const parent=new Element();
  syncMessageRows(parent,[m],{expandableText,nick:x=>x,avatarHTML:()=>'',ago:()=>''});
  const fold=nodes(parent).find(n=>n.tagName==='details');
  assert.equal(fold.open,false);
  assert.equal(fold.children[0].textContent,'자세히');
  assert.equal(nodes(fold).find(n=>n.tagName==='pre').textContent,source);
  assert.ok(nodes(parent).find(n=>n.className==='pi-prose').textContent.includes(translated.text));
});
test('failed translation shows the original and a failure label',async()=>{
  const {syncMessageRows,messageFromEvent}=await load('ui/messenger.js');
  const {expandableText}=await load('ui/decide.js');
  const parent=new Element();
  const m=messageFromEvent({...ev,data:{...ev.data,pi_display:{status:'failed'}}});
  syncMessageRows(parent,[m],{expandableText,nick:x=>x,avatarHTML:()=>'',ago:()=>''});
  assert.ok(parent.textContent.includes('번역 못 함'));
  assert.ok(parent.textContent.includes('Please compare the tables.'));
});
test('approval uses the chief explanation and leaves original untouched',async()=>{
  const {displaySummary}=await load('ui/decide.js');
  const card={summary:'New-Item -Force .tmp',pi_summary:'기존 설명',pi_display:translated};
  assert.equal(displaySummary(card),translated.text);
  assert.equal(card.summary,'New-Item -Force .tmp');
});
test('translation arriving during a history fetch survives the older response',async()=>{
  const {createMessenger}=await load('ui/messenger.js');
  let finish;
  const h=createMessenger({fetchPage:()=>new Promise(resolve=>{finish=resolve;})});
  const pending=h.loadOlder();
  h.ingest({type:'messenger.translated',data:{message_seq:1,pi_display:translated}});
  finish({events:[ev],has_more:false}); await pending;
  assert.equal(h.items[0].piDisplay.text,translated.text);
});
