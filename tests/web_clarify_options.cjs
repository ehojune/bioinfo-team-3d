const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');

// textContent only: an HTML insertion of a model-written question fails the test.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('clarify cards must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const source=fs.readFileSync(path.join(web,'ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const nodes=el=>[el,...el.children.flatMap(nodes)];
const COHORT={question:'Which cohort <b>?',options:['cases','controls'],allow_free_text:true,depth:60};
const GENOME={question:'Which genome build?',options:['GRCh38','GRCh37','T2T'],allow_free_text:false};
const FREE={question:'Anything else?',options:[],allow_free_text:true};
const approval={id:'q1',kind:'clarify',summary:'Please answer before work begins',created_at:1,
  detail:{questions:[COHORT,GENOME,FREE]}};

test('structured questions render as option buttons, free inputs and a depth label',async()=>{
  const decide=await modulePromise,container=new Element();
  const decisions=[];
  const [row]=decide.syncDecisionCards(container,[approval],[],{onDecision:(a,ok,note)=>decisions.push([ok,note])});
  const parts=row._decisionParts;
  assert.equal(parts.detail.hidden,true,'questions are not dumped as raw JSON detail');
  const blocks=parts.questions.children;
  assert.equal(blocks.length,3);
  assert.match(blocks[0].textContent,/1\. Which cohort <b>\?/);
  assert.match(blocks[0].textContent,/깊이 약 60분/);
  const choices=i=>nodes(blocks[i]).filter(n=>n.tagName==='BUTTON');
  assert.deepEqual(choices(0).map(b=>b.textContent),['a) cases','b) controls']);
  assert.deepEqual(choices(1).map(b=>b.textContent),['a) GRCh38','b) GRCh37','c) T2T']);
  assert.ok(choices(0).every(b=>b.type==='button'&&!b.dataset.act),'option buttons never submit the card');
  const inputs=i=>nodes(blocks[i]).filter(n=>n.tagName==='INPUT');
  assert.equal(inputs(0).length,1);assert.equal(inputs(1).length,0,'free text only where allowed');
  assert.equal(inputs(2).length,1);

  assert.equal(decide.decisionNote(row,true),'','an unanswered question leaves the answer empty');
  choices(0)[0].onclick();choices(0)[1].onclick();
  assert.deepEqual(choices(0).map(b=>b.ariaPressed),['false','true'],'one choice per question');
  choices(1)[0].onclick();
  assert.equal(decide.decisionNote(row,true),'','the free-text-only question still needs an answer');
  inputs(2)[0].value='  none  ';
  assert.equal(decide.decisionNote(row,true),'Q1. b) controls\nQ2. a) GRCh38\nQ3. none');
  inputs(0)[0].value='only donors > 3';parts.note.value='memo';
  assert.equal(decide.decisionNote(row,true),'Q1. b) controls — only donors > 3\nQ2. a) GRCh38\nQ3. none\n메모: memo');
  assert.equal(decide.decisionNote(row,false),'memo','a refusal keeps only the typed note');

  decide.syncDecisionCards(container,[{...approval}],[],{onDecision:(a,ok,note)=>decisions.push([ok,note])});
  assert.equal(choices(0)[1].ariaPressed,'true','a live re-render keeps the choice');
  assert.equal(inputs(2)[0].value,'  none  ');
  parts.approve.onclick();parts.deny.onclick();
  assert.deepEqual(decisions,[[true,'Q1. b) controls — only donors > 3\nQ2. a) GRCh38\nQ3. none\n메모: memo'],[false,'memo']]);
  choices(1)[0].onclick();
  assert.equal(decide.decisionNote(row,true),'','pressing the chosen option again clears it');
});

test('legacy string questions keep the single answer box',async()=>{
  const decide=await modulePromise,container=new Element();
  const [row]=decide.syncDecisionCards(container,[{id:'q2',kind:'clarify',summary:'Which cohort?',detail:'Which cohort?'}]);
  assert.equal(row._decisionParts.questions.hidden,true);
  row._decisionParts.note.value=' cases ';
  assert.equal(decide.decisionNote(row,true),'cases');
  const [plain]=decide.syncDecisionCards(container,[{id:'q3',kind:'clarify',summary:'s',
    detail:{questions:[{question:'Which cohort?',options:[],allow_free_text:true}]}}]);
  plain._decisionParts.questions.children[0].children.find(n=>n.tagName==='INPUT').value='cases';
  assert.equal(decide.decisionNote(plain,true),'Q1. cases');
});

test('both offices send the composed answer through the existing approval path',()=>{
  const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
  const live=fs.readFileSync(path.join(web,'lab3d/src/live.js'),'utf8');
  assert.match(html,/import \{[^}]*decisionNote[^}]*\} from '\.\/ui\/decide\.js'/);
  assert.match(html,/decisionNote\(li, act === 'approve'\)/);
  assert.match(html,/\.clarify-q/);
  assert.match(live,/a\.kind === 'clarify' && approved && !note/);
});
