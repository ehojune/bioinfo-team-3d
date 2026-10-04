const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// #57 ⑦: the capability card comes from the gateway's roster facts and survives snapshot and roster updates.
const root = path.resolve(__dirname, '..');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8'), context);
const live = context.LabHQState.createOfficeState();
const capabilities = {resume: true, read_only: true, effort: 'high', permission: 'read-only', max_turns: 40,
  mcp: ['labhq_approval']};
live.apply({type: 'snapshot', data: {agents: [{id: 'engineer', engine: 'codex', capabilities}], requests: [], approvals: []}});
assert.deepEqual(JSON.parse(JSON.stringify(live.S.agents.get('engineer').capabilities)), capabilities);
live.apply({type: 'roster.updated', data: {agents: [{id: 'engineer', engine: 'codex',
  capabilities: {...capabilities, effort: 'medium'}}]}});
assert.equal(live.S.agents.get('engineer').capabilities.effort, 'medium', 'a roster update replaces the card');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
for (const label of ['권한', '추론 강도', '이어 묻기', '읽기 전용 상담', 'MCP']) assert.match(html, new RegExp(`'${label}'`));
assert.match(html, /a\.capabilities/);
console.log('agent capability web tests passed');
