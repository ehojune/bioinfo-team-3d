const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// #57 ⑦ follow-up: the request line names who spent the money when the summary carries by_agent.
const root = path.resolve(__dirname, '..');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8'), context);
const {agentCostLabel} = context.LabHQState;
const names = {cso: '부엉이', analyst: '너구리'};
const summary = {by_agent: {
  analyst: {actual_usd: 0.1, estimated_usd: 0, unknown_count: 1},
  cso: {actual_usd: 0.4, estimated_usd: 0, unknown_count: 0},
  unknown: {actual_usd: 0, estimated_usd: 0.2, unknown_count: 0}}};
assert.equal(agentCostLabel(summary, id => names[id]),
  '너구리 확인 $0.10 + 미집계 1건 · 부엉이 확인 $0.40 · 직원 기록 없음 추정 $0.20');
assert.equal(agentCostLabel({by_engine: {}}), '', 'an older summary without by_agent adds nothing');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
assert.match(html, /agentCostLabel\(q\.costSummary, nick\)/);
assert.match(html, /JSON\.stringify\(\[S\.requests\.size, staffNames,/, 'a roster change redraws the cost line');
console.log('cost by agent web tests passed');
