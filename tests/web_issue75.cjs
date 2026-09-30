const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');

class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.className='';this.textContent='';this.onclick=null; }
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[];this.append(...children);}
  querySelectorAll(selector){
    const all=[];const visit=node=>{for(const child of node.children){all.push(child);visit(child);}};visit(this);
    if(selector.startsWith('.'))return all.filter(x=>String(x.className).split(/\s+/).includes(selector.slice(1)));
    if(selector==='[data-request-id]')return all.filter(x=>x.dataset.requestId);
    return [];
  }
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
global.document={createElement:tag=>new Element(tag)};

(async()=>{
  const source=fs.readFileSync(path.join(root,'ui/decide.js'),'utf8');
  const decide=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const history=Array.from({length:12},(_,i)=>({
    approval:{id:`a${i}`,request_id:i<7?'r1':'r2',kind:'budget',summary:`결정 ${i}`},
    approved:true,decided_at:100-i,
  }));
  const container=new Element();
  decide.syncDecisionHistory(container,history,{kindLabels:{budget:'예산'}});
  assert.equal(container.querySelectorAll('.decision-history-item').length,10,'기본 결정 이력은 최근 10건 이하');
  assert.equal(container.querySelectorAll('[data-request-id]').length,2,'결정 이력은 요청별로 묶인다');
  const more=container.querySelector('.decision-history-more');
  assert.ok(more,'남은 결정 이력을 여는 더 보기 버튼이 있다');
  more.onclick();
  assert.equal(container.querySelectorAll('.decision-history-item').length,12,'더 보기로 남은 이력을 연다');

  const html=fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.match(html,/id="staff-toggle"/,'폰 직원 줄을 여닫는 버튼이 있다');
  assert.match(html,/@media \(max-width:620px\)[\s\S]*?\.staff-wrap:not\(\.open\) \.staff-strip\s*\{display:none\}/,
    '폰에서는 직원 줄이 기본으로 접힌다');
  console.log('mobile staff collapse and bounded grouped decision history: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
