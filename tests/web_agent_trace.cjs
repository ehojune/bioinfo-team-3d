const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// #57 ⑥: thinking and debug lines are kept per agent for the staff sheet, never in the feed or activity.
const root = path.resolve(__dirname, '..');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8'), context);
const live = context.LabHQState.createOfficeState();
live.apply({type: 'snapshot', data: {agents: [{id: 'analyst', name: 'Analyst'}], requests: [], approvals: []}});
const log = (data, ts) => live.apply({type: 'agent.log', agent_id: 'analyst', ts, data});
log({text: 'Loaded the matrix'}, 10);
log({level: 'thinking', text: 'Compare the two normalizations first'}, 11);
log({level: 'debug', text: '$ codex (12 args)'}, 12);

const a = live.S.agents.get('analyst');
assert.deepEqual(Array.from(a.trace, l => [l.level, l.text]),
  [['thinking', 'Compare the two normalizations first'], ['debug', '$ codex (12 args)']]);
assert.deepEqual(Array.from(a.log, l => l.text), ['Loaded the matrix'], 'activity keeps ordinary lines only');
assert.equal(a.say, 'Loaded the matrix', 'a thinking line is not the speech bubble');
assert.ok(!live.S.feed.some(item => /normalizations|codex \(/.test(item.text)), 'the feed never shows trace lines');

for (let i = 0; i < 100; i++) log({level: 'thinking', text: `step ${i}`}, 100 + i);
assert.equal(a.trace.length, 60, 'the trace is a bounded ring buffer');
assert.equal(a.trace[59].text, 'step 99');
assert.equal(a.log.length, 1, 'a burst of thinking does not push out activity');

live.apply({type: 'roster.updated', data: {agents: [{id: 'analyst', name: 'Analyst', role: 'analysis'}]}});
assert.equal(live.S.agents.get('analyst').trace.length, 60, 'a roster update keeps the trace');
assert.ok(!Object.keys(live.S.agents.get('analyst')).includes('trace'), 'the trace is not part of compared state');

const html = fs.readFileSync(path.join(root, 'labhq/web/index.html'), 'utf8');
assert.match(html, /data-view="trace"/);
assert.match(html, /생각·디버그/);
assert.match(html, /a\.trace/);
console.log('agent trace web tests passed');
