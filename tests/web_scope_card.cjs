const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');

// #36: an out-of-scope request asks the PI on a card with two buttons, proceed or stop, and no typed answer.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('decision cards must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const source=fs.readFileSync(path.join(web,'ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const card={id:'s',kind:'scope',request_id:'r',summary:'이 요청은 랩 범위 밖으로 보입니다: fluid dynamics. 진행할까요?',
  created_at:1,detail:{gate:'scope',verdict:'out',reason:'fluid dynamics',steps:5}};

test('the scope card offers proceed and stop and sends a plain decision',async()=>{
  const decide=await modulePromise,container=new Element(),sent=[];
  const [row]=decide.syncDecisionCards(container,[card],[],{kindLabels:{scope:'범위 확인'},
    onDecision:(a,ok,note,type,choice)=>sent.push([ok,choice])});
  const p=row._decisionParts;
  assert.equal(p.title.textContent,'범위 확인');
  assert.equal(p.approve.textContent,'진행');
  assert.equal(p.deny.textContent,'중단');
  assert.equal(p.revise.hidden,true);
  p.approve.onclick(); p.deny.onclick();
  assert.deepEqual(sent,[[true,null],[false,null]]);
  assert.match(fs.readFileSync(path.join(web,'state.js'),'utf8'),/scope: '범위 확인'/);
});
