const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');

class ClassList {
  constructor(el){this.el=el;this.values=new Set();}
  add(...values){values.forEach(v=>this.values.add(v));this.el.className=[...this.values].join(' ');}
  remove(...values){values.forEach(v=>this.values.delete(v));this.el.className=[...this.values].join(' ');}
  toggle(value,on){on===undefined?(this.values.has(value)?this.values.delete(value):this.values.add(value)):
    (on?this.values.add(value):this.values.delete(value));this.el.className=[...this.values].join(' ');}
}
class Element {
  constructor(tag='div') { this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.dataset={};
    this.textContent='';this.value='';this.listeners={};this.className='';this.classList=new ClassList(this);this.hidden=false; }
  append(...children){for(const child of children){child.parentNode=this;this.children.push(child);}}
  prepend(child){child.parentNode=this;this.children.unshift(child);}
  replaceChildren(...children){this.children=[];this.append(...children);}
  insertBefore(child,ref){this.removeChild(child);child.parentNode=this;this.children.splice(this.children.indexOf(ref),0,child);return child;}
  removeChild(child){this.children=this.children.filter(x=>x!==child);return child;}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(x=>x!==this);}
  addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
  setAttribute(name,value){if(name.startsWith('data-'))this.dataset[name.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(value);else this[name]=String(value);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){
    const all=[];const visit=node=>{for(const child of node.children){all.push(child);visit(child);}};visit(this);
    if(selector==='textarea')return all.filter(x=>x.tagName==='TEXTAREA');
    if(selector==='button')return all.filter(x=>x.tagName==='BUTTON');
    if(selector==='[data-decision-key]')return all.filter(x=>x.dataset.decisionKey);
    if(selector==='[data-agent]')return all.filter(x=>x.dataset.agent);
    return [];
  }
}
global.document={createElement:tag=>new Element(tag)};
async function load(name){
  const source=fs.readFileSync(path.join(root,'ui',name),'utf8');
  return import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
}
(async()=>{
  const decide=await load('decide.js');
  const container=new Element('div');
  const one={id:'a1',kind:'clarify',summary:'Step s1 needs a PI decision:\n첫 질문',request_id:'r1',created_at:10,timeout_s:60};
  const requests=new Map([['r1',{plan:[{id:'s1',depends_on:[]},{id:'s2',depends_on:['s1']},{id:'s3',depends_on:['s2']}]}]]);
  decide.syncDecisionCards(container,[one],[],{now:()=>20,requests});
  const first=container.querySelector('[data-decision-key]');
  const draft=first.querySelector('textarea');draft.value='쓰던 답';
  decide.syncDecisionCards(container,[one,{id:'a2',kind:'budget',summary:'새 승인',created_at:30}],[],{now:()=>31,requests});
  assert.equal(container.querySelectorAll('[data-decision-key]').length,2);
  assert.equal(container.querySelectorAll('[data-decision-key]')[0].dataset.id,'a2','newest decision comes first');
  assert.equal(container.querySelectorAll('[data-decision-key]')[1],first,'keyed node is reused');
  assert.equal(first.querySelector('textarea').value,'쓰던 답','draft survives another approval');
  assert.equal(container.querySelectorAll('textarea').length,2,'every decision has a note');
  assert.match(first._decisionParts.timing.textContent,/21초 대기 · 1분 남음 · 하류 2단계 멈춤/);

  const strip=await load('strip.js');
  const staff=new Element('div');
  strip.syncStaffCards(staff,[{id:'a',name:'A',role:'one'},{id:'b',name:'B',role:'two'}],{visual:a=>a.state||'idle'});
  strip.syncStaffCards(staff,[{id:'a',name:'A',role:'one'},{id:'b',name:'B',role:'two'},{id:'c',name:'C',role:'three'}],{visual:a=>a.state||'idle'});
  assert.equal(staff.querySelectorAll('[data-agent]').length,3,'card count follows roster');
  strip.syncStaffCards(staff,[{id:'done',name:'Done',state:'done'},{id:'error',name:'Error',state:'error'},{id:'queued',name:'Queued',state:'queued'}],{visual:a=>a.state});
  const statusCards=staff.querySelectorAll('[data-agent]');
  assert.equal(statusCards[0]._staffParts.status.textContent,'완료');
  assert.equal(statusCards[1]._staffParts.status.textContent,'오류');
  assert.match(statusCards[1].className,/staff-error/,'오류 카드는 색 구분용 class를 가진다');
  assert.equal(statusCards[2]._staffParts.status.textContent,'순서 대기');

  const shell=await load('shell.js');
  const nav=new Element('nav'),panels=[new Element('section'),new Element('section')];
  panels[0].dataset.tab='decisions';panels[1].dataset.tab='tasks';
  shell.activateTab(nav,panels,'decisions');
  assert.equal(panels[0].hidden,false);assert.equal(panels[1].hidden,true);
  console.log('keyed decisions, staff strip and tabs: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
