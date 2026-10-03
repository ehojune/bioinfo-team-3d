const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');

// A step's PI question puts each choice on its own line (STEP_PROMPT). The card draws it with textContent, so the
// line breaks show only when the card's CSS keeps them; any HTML insertion is a test failure.
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.value='';this._text=''; }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('decision summaries must never use innerHTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
}
global.document={createElement:tag=>new Element(tag)};
const web=path.join(__dirname,'../labhq/web');
const source=fs.readFileSync(path.join(web,'ui/decide.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

// Top-level declarations of every rule whose selector list names `.ap .sum`, in source order.
function summaryDeclarations(file) {
  const html=fs.readFileSync(path.join(web,file),'utf8');
  const css=[...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]).join('\n');
  const found={};
  for (const [,selectors,body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.split(',').some(s=>s.trim().replace(/\s+/g,' ')==='.ap .sum')) continue;
    for (const decl of body.split(';')) {
      const at=decl.indexOf(':');
      if (at>0) found[decl.slice(0,at).trim()]=decl.slice(at+1).trim();
    }
  }
  return found;
}

test('the summary keeps the line breaks of a card question in 2.5D and 3D',()=>{
  for (const file of ['index.html','lab3d/index.html']) {
    const decl=summaryDeclarations(file);
    assert.equal(decl['white-space'],'pre-wrap',file+': line breaks of the summary must show');
    assert.equal(decl['overflow-wrap'],'anywhere',file+': a long line must still wrap');
  }
});

test('the summary is the literal text, line breaks included',async()=>{
  const decide=await modulePromise,container=new Element();
  const summary='Step s1 needs a PI decision:\nWhich group <b>first</b>?\n- cases\n- controls';
  const [row]=decide.syncDecisionCards(container,[{id:'q1',kind:'clarify',summary}],[],{tagName:'article'});
  const part=row._decisionParts.summary;
  assert.equal(part.tagName,'P');
  assert.equal(part.className,'sum');
  assert.equal(part.textContent,summary);
});
