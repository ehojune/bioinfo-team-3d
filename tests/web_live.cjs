const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));
// A small DOM/transport harness exercises decisions and reconnects without a browser dependency.
class Element {
  constructor(tag='div') { this.tag=tag; this.tagName=tag.toUpperCase(); this.children=[]; this.textContent=''; this.listeners={}; this.dataset={}; this.value=''; }
  append(...children) { for(const child of children)this.children.push(child); }
  replaceChildren(...children) { this.children=[];this.append(...children); }
  insertBefore(child,ref) { this.removeChild(child);this.children.splice(this.children.indexOf(ref),0,child);return child; }
  removeChild(child) { this.children=this.children.filter(x=>x!==child);return child; }
  addEventListener(type, callback) { this.listeners[type]=callback; }
  querySelector(selector){
    const all=[];const visit=node=>{for(const child of node.children){all.push(child);visit(child);}};visit(this);
    if(selector==='textarea')return all.find(e=>e.tagName==='TEXTAREA')||null;
    return null;
  }
}
const elements = new Map();
const element = id => { if(!elements.has(id))elements.set(id,new Element());return elements.get(id); };
const textIn = el => el.textContent + el.children.map(textIn).join('');
global.document = {getElementById:element,createElement:tag=>new Element(tag),querySelector:element,
  body:{classList:{add(){}}}};
global.location = {search:'?token=test-client',pathname:'/3d/',protocol:'http:',host:'example.invalid'};
let saved, cleanURL;
global.localStorage = {getItem(){return saved;},setItem(k,v){saved=v;}};
global.history = {replaceState(a,b,url){cleanURL=url;}};
const timers=[];const intervals=[];let clock=1000;
Date.now=()=>clock*1000;
global.setTimeout = callback => { timers.push(callback);return timers.length; };
global.clearTimeout = () => {};
global.setInterval = callback => { intervals.push(callback);return intervals.length; };
const sockets=[];
global.WebSocket = class {
  constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}
  send(message){this.sent.push(JSON.parse(message));}
  close(){this.readyState=3;this.onclose?.({code:1000});}
};
const decide = fs.readFileSync(path.join(root,'ui/decide.js'),'utf8');
const decideUrl = 'data:text/javascript;base64,'+Buffer.from(decide).toString('base64');
const source = fs.readFileSync(path.join(root,'lab3d/src/live.js'),'utf8').replace("'../../ui/decide.js'", `'${decideUrl}'`);
(async()=>{
  const {startLiveOffice} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  let S, visual, shown;
  startLiveOffice((state, displayState)=>{S=state;visual=displayState;shown=state.agents.get('analyst') && displayState(state.agents.get('analyst'));});
  assert.equal(saved,'test-client');assert.equal(cleanURL,'/3d/');
  let ws=sockets[0]; assert(!ws.url.includes('since='));
  ws.readyState=1;ws.onopen();
  const event = ev => ws.onmessage({data:JSON.stringify(ev)});
  event({type:'snapshot',seq:10,data:{agents:[{id:'analyst'}]}});
  event({type:'agent.usage',seq:11,data:{cost_usd:1}});
  event({type:'agent.usage',seq:11,data:{cost_usd:1}});
  assert.equal(S.cost,1,'duplicate seq ignored');
  event({type:'approval.requested',seq:12,data:{id:'a1',summary:'<img src=x>',kind:'tool_permission',
    created_at:clock,detail:{tool_name:'Bash',input:JSON.stringify({command:'printf "<example>"',args:['--example']})}}});
  let row=element('approvals').children[0].children[0];
  assert.equal(row._decisionParts.summary.textContent,'<img src=x>','untrusted text is never HTML');
  assert.match(textIn(row._decisionParts.detail),/Bash/);
  assert.match(textIn(row._decisionParts.detail),/printf.*<example>/s,'3D retains tool command input');
  assert.equal(intervals.length,1,'3D starts one periodic decision-card clock');
  clock+=30;intervals[0]();
  row=element('approvals').children[0].children[0];
  assert.match(row._decisionParts.timing.textContent,/30초 대기/,'3D waiting time advances without an event');
  row._decisionParts.approve.onclick();
  assert.deepEqual(ws.sent,[{type:'approval.resolve',id:'a1',approved:true,note:''}]);
  row._decisionParts.approve.onclick();
  assert.equal(ws.sent.length,1,'pending decision cannot be double sent');
  event({type:'approval.resolved',seq:13,data:{id:'a1',approved:true}});
  assert.equal(S.approvals.size,0);
  event({type:'approval.requested',seq:14,data:{id:'a2',kind:'hpc_submit',summary:'Reject example',
    detail:{queue:'example.q',script_path:'jobs/example.sh',script_preview:'#!/bin/bash\nprintf example',cores:4}}});
  row=element('approvals').children[0].children[0];
  for(const value of ['example.q','jobs/example.sh','#!/bin/bash\nprintf example','cores']) {
    assert.ok(textIn(row._decisionParts.detail).includes(value),'3D retains HPC '+value);
  }
  row._decisionParts.deny.onclick();
  assert.equal(ws.sent.at(-1).approved,false);
  event({type:'approval.stale',seq:15,data:{id:'a2'}});assert.equal(S.approvals.size,0);
  event({type:'agent.status',agent_id:'analyst',seq:16,ts:Date.now()/1000,data:{state:'done'}});
  assert.equal(shown,'done');assert.equal(timers.length,1,'done schedules a delayed 3D render');
  S.agents.get('analyst').stateAt-=4;
  timers.shift()();assert.equal(shown,'idle','3D display returns to waiting after three seconds');
  assert.equal(S.agents.get('analyst').state,'done','display must not change event state');
  ws.close();assert.equal(S.conn,'offline');assert.equal(element('token-form').hidden,false);
  timers.shift()();ws=sockets.at(-1);assert(ws.url.endsWith('&since=16'));
  ws.readyState=1;ws.onopen();
  event({type:'snapshot',seq:2,replay_gap:{requested_since:15},data:{agents:[],requests:[],approvals:[]}});
  assert.equal(S.agents.size,0);assert.equal(S.cost,0);assert.equal(S.requests.size,0);
  event({type:'agent.usage',seq:3,data:{cost_usd:2}});assert.equal(S.cost,2,'snapshot resets seq even after gateway rollback');
  ws.onclose({code:1008});assert.equal(S.conn,'auth');assert.equal(element('token-form').hidden,false);
  assert.equal(timers.length,0,'auth rejection does not loop');
  console.log('live auth, decisions, stale approvals, duplicate seq, reconnect and replay gap: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
