const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
class Element {
  constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text='';}
  set textContent(value) {this._text=String(value);this.children=[];}
  get textContent() {return this._text+this.children.map(n=>n.textContent).join('');}
  set innerHTML(value) {throw Error('must use textContent');}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=[];this.append(...nodes);}
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const source=fs.readFileSync(path.join(web,'ui/decide.js'),'utf8');
const mod=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('local default and editable private new repository survive redraw',async()=>{
  const ui=await mod, host=new Element();
  const card={id:'s1',kind:'result_storage',detail:{storage:{github_available:true,suggested_name:'study'}}};
  const [row]=ui.syncDecisionCards(host,[card]);
  assert.equal(ui.storageChoice(row).mode,'local');
  const box=row._decisionParts.storage;
  box._mode.value='new';box._mode.onchange();box._repo.value='my-study';
  ui.syncDecisionCards(host,[card]);
  assert.deepEqual(ui.storageChoice(row),{mode:'new',repo:'my-study',visibility:'private'});
});
test('missing token disables GitHub choices and clarify keeps one card',async()=>{
  const ui=await mod, host=new Element();
  const [row]=ui.syncDecisionCards(host,[{id:'s2',kind:'clarify',detail:{questions:['Which study?'],
    storage:{github_available:false,warning:'토큰 설정 필요'}}}]);
  assert.equal(host.children.length,1);
  assert.equal(row._decisionParts.storage._mode.children[1].disabled,true);
  assert.match(row.textContent,/토큰 설정 필요/);
});
test('both offices pass structured storage choices',()=>{
  assert.match(fs.readFileSync(path.join(web,'index.html'),'utf8'),/storageChoice\(li\)/);
  assert.match(fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8'),/if \(storage\) decision\.storage = storage/);
});

test('PI-opened late storage stays answerable without a countdown',async()=>{
  const ui=await mod, host=new Element();
  const card={id:'late',kind:'result_storage',created_at:1,
    detail:{late_storage:true,storage:{github_available:true,suggested_name:'study'}}};
  const [row]=ui.syncDecisionCards(host,[card],[],{now:()=>1e9});
  assert.doesNotMatch(row.textContent,/시간 초과|남음/);
  assert.equal(row._decisionParts.approve.disabled,false);
});

test('end storage card shows the first choice and can keep or change it',async()=>{
  const ui=await mod, host=new Element();
  const card={id:'end',kind:'result_storage',detail:{storage:{github_available:true,default:'keep',allow_keep:true,
    initial_label:'새 GitHub 저장소 owner/study',suggested_name:'study'}}};
  const [row]=ui.syncDecisionCards(host,[card]);
  assert.match(row.textContent,/처음 선택: 새 GitHub 저장소 owner\/study/);
  assert.equal(ui.storageChoice(row).mode,'keep');
  const box=row._decisionParts.storage;
  box._mode.value='local';box._mode.onchange();
  assert.equal(ui.storageChoice(row).mode,'local');
});

test('publishing waits for PI review and preserves the editor preview',async()=>{
  const ui=await mod, host=new Element();
  const request={pi_review_pending:true,pi_reviewed_at:null};
  const requests=new Map([['r',request]]);
  const card={id:'publish',kind:'result_publish',request_id:'r',detail:{documents:{'README.md':'Editor draft'}}};
  const [row]=ui.syncDecisionCards(host,[card],[],{requests});
  assert.equal(row._decisionParts.approve.disabled,true);
  assert.equal(row._decisionParts.deny.disabled,false);
  assert.match(row.textContent,/Editor draft/);
  assert.match(row.textContent,/보고서 검토·확인을 완료/);
  request.pi_review_pending=false; request.pi_reviewed_at=100;
  const [reviewed]=ui.syncDecisionCards(host,[card],[],{requests});
  assert.equal(reviewed,row);
  assert.equal(reviewed._decisionParts.approve.disabled,false);
  assert.match(reviewed.textContent,/Editor draft/);
  ui.syncDecisionCards(host,[card],[],{requests,disabled:()=>true});
  assert.equal(reviewed._decisionParts.approve.disabled,true);
});

test('publish card names the PDF line, included or skipped',async()=>{
  const ui=await mod, host=new Element();
  const skipped={id:'p1',kind:'result_publish',request_id:'r',detail:{documents:{'README.md':'draft'},
    pdf:'PDF는 포함하지 않았어요: 이 PC에서 Edge나 Chrome을 찾지 못했어요.'}};
  const [row]=ui.syncDecisionCards(host,[skipped],[],{requests:new Map([['r',{pi_reviewed_at:1}]])});
  assert.match(row.textContent,/보고서 PDF/);
  assert.match(row.textContent,/PDF는 포함하지 않았어요: 이 PC에서 Edge나 Chrome을 찾지 못했어요\./);
  const included={id:'p2',kind:'result_publish',request_id:'r',detail:{documents:{'README.md':'draft'},pdf:'포함: report.pdf (1.2 MB)'}};
  const [row2]=ui.syncDecisionCards(new Element(),[included],[],{requests:new Map([['r',{pi_reviewed_at:1}]])});
  assert.match(row2.textContent,/포함: report\.pdf \(1\.2 MB\)/);
});

test('publish card previews base64 SVG as an image and rejects inline SVG markup',async()=>{
  const ui=await mod, host=new Element();
  const safe='data:image/svg+xml;base64,'+Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').toString('base64');
  const card={id:'svg',kind:'result_publish',request_id:'r',detail:{documents:{},images:{'safe.svg':safe,
    'unsafe.svg':'data:image/svg+xml,<svg onload="alert(1)"></svg>'}}};
  const [row]=ui.syncDecisionCards(host,[card],[],{requests:new Map([['r',{pi_reviewed_at:1}]])});
  const walk=node=>[node,...node.children.flatMap(walk)];
  const images=walk(row).filter(node=>node.tagName==='IMG');
  assert.equal(images.length,1);
  assert.equal(images[0].src,safe);
  assert.equal(images[0].alt,'safe.svg');
});

test('publish card shows the size and local path when an image preview is omitted',async()=>{
  const ui=await mod, host=new Element();
  const card={id:'large',kind:'result_publish',request_id:'r',detail:{documents:{},images:{},
    image_preview_omitted:{'large.png':{size_bytes:1500000,path:'C:/draft/large.png'}}}};
  const [row]=ui.syncDecisionCards(host,[card],[],{requests:new Map([['r',{pi_reviewed_at:1}]])});
  assert.match(row.textContent,/large\.png/);
  assert.match(row.textContent,/1\.4 MB/);
  assert.match(row.textContent,/C:\/draft\/large\.png/);
});
