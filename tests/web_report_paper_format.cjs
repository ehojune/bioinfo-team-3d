const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const web = path.join(__dirname, '../labhq/web');
require(path.join(web, 'state.js'));

test('science and operations survive snapshot, completion and full-text reload separately', () => {
  const office = globalThis.LabHQState.createOfficeState({now: () => 10});
  office.apply({type: 'snapshot', data: {agents: [], requests: [{id: 'r', status: 'done', report: '# 제목',
    report_appendix: '## 부록\nscience', execution_record: 'operations', execution_record_truncated: true}]}});
  const q = office.S.requests.get('r');
  assert.equal(q.report_appendix, '## 부록\nscience');
  assert.equal(q.execution_record, 'operations');
  assert.equal(q.execution_record_truncated, true);
  office.apply({type: 'request.completed', request_id: 'r', data: {ok: true, execution_record: 'complete operations'}});
  assert.equal(q.execution_record, 'complete operations');
  assert.equal(q.execution_record_truncated, undefined);
  office.fillRequestDetail('r', {report_appendix: '## 부록\nfull science', execution_record: 'full operations'});
  assert.equal(q.execution_record, 'full operations');
  assert.equal(q.report_appendix, '## 부록\nfull science');
});

test('report view labels science and operations separately and escapes both', () => {
  const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
  const start = html.indexOf('const reportHTML = '), end = html.indexOf('// A finished request', start);
  const ctx = vm.createContext({esc: s => String(s).replaceAll('<', '&lt;'), fuLoading: new Set(), api: {}});
  vm.runInContext(html.slice(start, end) + '\nthis.render = reportHTML;', ctx);
  const view = ctx.render({id: 'r', report: '# 제목', report_appendix: '## 부록\n<science>', execution_record: '<operations>'});
  assert.match(view, /<summary>과학 부록<\/summary>/);
  assert.match(view, /<summary>실행 기록<\/summary>/);
  assert.ok(view.includes('&lt;science>') && view.includes('&lt;operations>'));
  assert.match(ctx.render({id: 'old', report: 'old report', report_appendix: '## 부록: 실행 기록\nold log'}),
    /<summary>실행 기록<\/summary>/);
});
