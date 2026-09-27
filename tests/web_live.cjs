const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));
// A small DOM/transport harness exercises decisions and reconnects without a browser dependency.
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.textContent=''; this.listeners={}; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children=[]; }
  addEventListener(type, callback) { this.listeners[type]=callback; }
}
const elements = new Map();
const element = id => { if(!elements.has(id))elements.set(id,new Element());return elements.get(id); };
global.document = {getElementById:element,createElement:tag=>new Element(tag),querySelector:element,
  body:{classList:{add(){}}}};
global.location = {search:'?token=test-client',pathname:'/3d/',protocol:'http:',host:'example.invalid'};
let saved, cleanURL;
global.localStorage = {getItem(){return saved;},setItem(k,v){saved=v;}};
global.history = {replaceState(a,b,url){cleanURL=url;}};
const timers=[];
global.setTimeout = callback => { timers.push(callback);return timers.length; };
global.clearTimeout = () => {};
const sockets=[];
global.WebSocket = class {
  constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}
  send(message){this.sent.push(JSON.parse(message));}
  close(){this.readyState=3;this.onclose?.({code:1000});}
};
const source = fs.readFileSync(path.join(root,'lab3d/src/live.js'),'utf8');
(async()=>{
  const {startLiveOffice} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  let S;
  startLiveOffice(state=>{S=state;});
  assert.equal(saved,'test-client');assert.equal(cleanURL,'/3d/');
  let ws=sockets[0]; assert(!ws.url.includes('since='));
  ws.readyState=1;ws.onopen();
  const event = ev => ws.onmessage({data:JSON.stringify(ev)});
  event({type:'snapshot',seq:10,data:{agents:[{id:'analyst'}]}});
  event({type:'agent.usage',seq:11,data:{cost_usd:1}});
  event({type:'agent.usage',seq:11,data:{cost_usd:1}});
  assert.equal(S.cost,1,'duplicate seq ignored');
  event({type:'approval.requested',seq:12,data:{id:'a1',summary:'<img src=x>',kind:'tool_permission'}});
  let row=element('approvals').children[0];
  assert.equal(row.children[1].textContent,'<img src=x>','untrusted text is never HTML');
  row.children.find(e=>e.tag==='button').listeners.click();
  assert.deepEqual(ws.sent,[{type:'approval.resolve',id:'a1',approved:true,note:''}]);
  row.children.find(e=>e.tag==='button').listeners.click();
  assert.equal(ws.sent.length,1,'pending decision cannot be double sent');
  event({type:'approval.resolved',seq:13,data:{id:'a1',approved:true}});
  assert.equal(S.approvals.size,0);
  event({type:'approval.requested',seq:14,data:{id:'a2',summary:'Reject example'}});
  row=element('approvals').children[0];row.children.filter(e=>e.tag==='button')[1].listeners.click();
  assert.equal(ws.sent.at(-1).approved,false);
  event({type:'approval.stale',seq:15,data:{id:'a2'}});assert.equal(S.approvals.size,0);
  ws.close();assert.equal(S.conn,'offline');assert.equal(element('token-form').hidden,false);
  timers.shift()();ws=sockets.at(-1);assert(ws.url.endsWith('&since=15'));
  ws.readyState=1;ws.onopen();
  event({type:'snapshot',seq:2,replay_gap:{requested_since:15},data:{agents:[],requests:[],approvals:[]}});
  assert.equal(S.agents.size,0);assert.equal(S.cost,0);assert.equal(S.requests.size,0);
  event({type:'agent.usage',seq:3,data:{cost_usd:2}});assert.equal(S.cost,2,'snapshot resets seq even after gateway rollback');
  ws.onclose({code:1008});assert.equal(S.conn,'auth');assert.equal(element('token-form').hidden,false);
  assert.equal(timers.length,0,'auth rejection does not loop');
  console.log('live auth, decisions, stale approvals, duplicate seq, reconnect and replay gap: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
