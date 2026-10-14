const assert = require('node:assert/strict');
require('../labhq/web/state.js');
const office = global.LabHQState.createOfficeState({ now: () => 1000 });
office.apply({ type: 'snapshot', data: { agents: [], runners: [], requests: [
  { id: 'r', status: 'running', text: 'reproduce', plan: { steps: [] }, step_status: {} }
] } });
office.apply({ type: 'approval.requested', request_id: 'r', data: {
  id: 'hire', kind: 'recruit_plan', request_id: 'r', summary: '수습 통과하면 바로 투입',
  detail: { recruits: [{ paper: 'PMID:1' }, { paper: 'PMID:2' }] }
} });
office.apply({ type: 'request.step_quota_resumed', request_id: 'r', data: { step_id: 'hire', engine: 'mock' } });
assert.equal(office.S.requests.get('r').status, 'waiting_pi');
office.apply({ type: 'recruit.suggested', request_id: 'r', data: { handled: true,
  recruits: [{ paper: 'PMID:1', status: 'active' }, { paper: 'PMID:2', status: 'failed' }] } });
assert.equal(office.S.suggestions.length, 0, 'no second hire button after the batch card');
assert.equal(office.S.approvals.size, 1);
office.apply({ type: 'approval.resolved', request_id: 'r', data: { id: 'hire', approved: true } });
assert.equal(office.S.approvals.size, 0);
console.log('pre-plan recruitment card passed');
