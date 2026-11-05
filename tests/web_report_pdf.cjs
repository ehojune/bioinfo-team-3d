// "PDF로 받기" on the request report in the 2.5D and 3D views (#584).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const live3d = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');

function reportPdfHTML(api) {
  const start = html.indexOf('const reportPdfHTML = ');
  const end = html.indexOf('\nconst auditHTML', start);
  assert.ok(start > 0 && end > start, 'reportPdfHTML source found in the 2.5D page');
  const context = vm.createContext({
    esc: value => String(value).replace(/[&<>"]/g, c => `&#${c.charCodeAt(0)};`),
    LabHQState: global.LabHQState, api,
  });
  vm.runInContext(`${html.slice(start, end)}\nthis.reportPdfHTML = reportPdfHTML;`, context);
  return context.reportPdfHTML;
}

test('2.5D shows the button only for a finished request that has a report', () => {
  const render = reportPdfHTML({ reportPdf() {} });
  const button = render({ id: 'req_1', status: 'done', report: '# 결론' });
  assert.match(button, /<button class="btn report-pdf" type="button" data-rid="req_1">PDF로 받기<\/button>/);
  assert.match(render({ id: 'req_1', status: 'failed', report: '실패 보고' }), /report-pdf/);
  assert.equal(render({ id: 'req_1', status: 'running', report: '중간' }), '', 'no PDF while the request runs');
  assert.equal(render({ id: 'req_1', status: 'done', report: '' }), '', 'no PDF without a report');
  assert.equal(reportPdfHTML(undefined)({ id: 'req_1', status: 'done', report: 'x' }), '', 'no button without the api call');
  assert.match(render({ id: 'a"b', status: 'done', report: 'x' }), /data-rid="a&#34;b"/, 'the request id is escaped');
});

test('2.5D asks the gateway for the PDF with the token and saves PDF or HTML with the reason', () => {
  assert.match(html, /\$\{reportPdfHTML\(q\)\}/, 'the button sits in the request detail');
  assert.match(html, /reportPdf: async rid => \{[\s\S]*?\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/report-pdf[\s\S]*?Authorization: `Bearer \$\{token\}`/);
  assert.match(html, /X-Labhq-Report-Kind/);
  assert.match(html, /decodeURIComponent\(r\.headers\.get\('X-Labhq-Reason'\)/);
  assert.match(html, /classList\?\.contains\('report-pdf'\)/);
  assert.match(html, /link\.download = `labhq-report-\$\{rid\}\.\$\{kind\}`/, 'an HTML fallback is saved as .html');
  assert.match(html, /PDF를 만들지 못했어요: \$\{err\.message\}/, 'a refusal (controlled data) is shown to the PI');
});

test('3D has the same button, request and fallback handling', () => {
  assert.match(live3d, /text\(row, 'button', 'PDF로 받기'\)/);
  assert.match(live3d, /isTerminalRequest\(q\.status\)/);
  assert.match(live3d, /\/api\/requests\/\$\{encodeURIComponent\(rid\)\}\/report-pdf/);
  assert.match(live3d, /Authorization:`Bearer \$\{token\}`/);
  assert.match(live3d, /X-Labhq-Report-Kind/);
  assert.match(live3d, /decodeURIComponent\(response\.headers\.get\('X-Labhq-Reason'\)/);
  assert.match(live3d, /PDF를 만들지 못했어요: \$\{error\.message\}/);
});
