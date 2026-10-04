const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const create = context.LabHQState.createOfficeState;
const assumptions = ['GSE10072 선택 — 짝 자료', 'paired model — 환자가 분석 단위'];

const live = create();
live.apply({type:'request.plan', request_id:'r', data:{steps:[], assumptions}});
assert.deepEqual(Array.from(live.S.requests.get('r').assumptions), assumptions,
  'a live plan keeps assumptions for the web plan view');

const restored = create();
restored.apply({type:'snapshot', data:{requests:[{id:'r', status:'running', plan:{steps:[], assumptions}}]}});
assert.deepEqual(Array.from(restored.S.requests.get('r').assumptions), assumptions,
  'a late client restores assumptions from the saved plan');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
assert.match(html, /가정/);
assert.match(html, /q\.assumptions/);
assert.match(html, /실행 중 메모/);
