const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const create = context.LabHQState.createOfficeState;

const live = create();
live.apply({type:'request.created', request_id:'r', data:{text:'표', mode:'orchestrate', route:'auto'}});
live.apply({type:'request.route', request_id:'r', data:{mode:'solo', agent_id:'solo'}});
live.apply({type:'request.plan', request_id:'r', data:{steps:[{id:'A', agent_id:'worker', depends_on:[]}],
  processing:{mode:'solo', agent_id:'solo'}}});
assert.equal(live.S.requests.get('r').processing.mode, 'solo');
assert.equal(live.S.requests.get('r').plan.length, 1, 'fallback plan remains visible for a solo request');

live.apply({type:'request.route', request_id:'r', data:{mode:'team', agent_id:'solo', fallback:true}});
assert.equal(live.S.requests.get('r').processing.fallback, true);

const restored = create();
restored.apply({type:'snapshot', data:{requests:[{id:'r', status:'running', mode:'orchestrate',
  route_decision:{mode:'solo', agent_id:'solo'}, plan:{steps:[{id:'A', depends_on:[]}]}}]}});
assert.equal(restored.S.requests.get('r').processing.agent_id, 'solo');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
assert.match(html, /id="team-only"/);
assert.match(html, /route: \$\('#team-only'\)\.checked \? 'team' : 'auto'/);
assert.match(html, /처리: 단독 실패 → 팀/);
const live3d = fs.readFileSync(path.join(root, 'labhq/web/lab3d/src/live.js'), 'utf8');
assert.match(live3d, /처리: 단독/);
console.log('solo route web tests passed');
