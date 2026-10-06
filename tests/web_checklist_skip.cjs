const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'labhq/web/state.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source, context);
const create = context.LabHQState.createOfficeState;

// #446: a checklist item the plan could not do reaches the feed once, with topic/id and reason.
const live = create();
live.apply({type:'request.plan', request_id:'r', data:{steps:[], warnings:[
  '점검 못 함 bulk_rna_seq/independent_validation: 독립 코호트가 없다',
  'step A: added dependency on B referenced in instruction',
]}});
const lines = live.S.feed.filter(item => item.text.startsWith('점검 못 함'));
assert.equal(lines.length, 1, 'one feed line per skipped item');
assert.match(lines[0].text, /bulk_rna_seq\/independent_validation: 독립 코호트가 없다/);
assert.equal(lines[0].cls, 'alert');
assert.ok(!live.S.feed.some(item => item.text.includes('added dependency')), 'other plan warnings stay out of the feed');
// PR #452 review: a re-plan re-sends the whole warnings list; a line already shown is not posted again.
live.apply({type:'request.plan', request_id:'r', data:{steps:[], warnings:[
  '점검 못 함 bulk_rna_seq/independent_validation: 독립 코호트가 없다',
  '점검 못 함 bulk_rna_seq/batch: 처리 날짜 기록 없음',
]}});
const again = live.S.feed.filter(item => item.text.startsWith('점검 못 함'));
assert.equal(again.length, 2, 'only the new skip reaches the feed after a re-plan');
assert.ok(again.some(item => item.text.includes('bulk_rna_seq/batch: 처리 날짜 기록 없음')));
console.log('checklist skip web tests passed');
