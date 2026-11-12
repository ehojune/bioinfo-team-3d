const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');

require(path.join(__dirname, '../labhq/web/state.js'));

test('report markdown renders the common first screen, tables and safe relative links', () => {
  const markdown = `# 결과

## 질문
무엇인가?

## 답
3입니다 [[claim:analysis/main]].

## 한계
표본이 작습니다.

## 대표 그림
![그림](steps/analysis/outputs/plot.svg)

## 결과표
| gene | value |
|---|---:|
| A | 3 |

## 재현 안내
[재현](README.md#다시-실행)

## 근거 각주
- [근거 1]

## 실행 기록
[기록](execution_record.md)`;
  const html = globalThis.LabHQState.reportMarkdownHTML(markdown);
  const headings = [...html.matchAll(/<h3>([^<]+)<\/h3>/g)].map(match => match[1]);
  assert.deepEqual(headings, ['질문', '답', '한계', '대표 그림', '결과표', '재현 안내', '근거 각주', '실행 기록']);
  assert.match(html, /<table>/);
  assert.match(html, /href="README.md#다시-실행"/);
  assert.match(html, /<img src="steps\/analysis\/outputs\/plot.svg"/);
  assert.doesNotMatch(html, /\[\[claim:/);
});

test('report markdown escapes HTML and rejects executable links', () => {
  const html = globalThis.LabHQState.reportMarkdownHTML(
    '<img src=x onerror=alert(1)>\n\n[누르기](javascript:alert(1))\n\n[외부](https://example.org/result)');
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /javascript:/);
  assert.match(html, /href="https:\/\/example.org\/result"/);
});

test('external report images become links without automatic requests', () => {
  const html = globalThis.LabHQState.reportMarkdownHTML(
    '![외부 그림](https://tracker.example/pixel.png?request=secret)');
  assert.doesNotMatch(html, /<img[^>]+tracker\.example/);
  assert.match(html, /<a href="https:\/\/tracker\.example\/pixel\.png\?request=secret"/);
  assert.match(html, />외부 그림<\/a>/);
});

test('request report assets are inert until fetched with authentication', () => {
  const html = globalThis.LabHQState.reportMarkdownHTML(
    '![Plot](steps/s1/outputs/p.png)\n\n[Table](steps/s1/outputs/a.csv)', 'req_1');
  assert.match(html, /data-report-asset="steps\/s1\/outputs\/p.png"/);
  assert.match(html, /data-report-asset="steps\/s1\/outputs\/a.csv"/);
  assert.doesNotMatch(html, /(?:src|href)="steps\//);
});

test('2.5D passes the request id to the report renderer', () => {
  const html = fs.readFileSync(path.join(__dirname, '../labhq/web/index.html'), 'utf8');
  const start = html.indexOf('const reportHTML = '), end = html.indexOf('\n// A finished request', start);
  assert.ok(start >= 0 && end > start);
  const context = vm.createContext({LabHQState: globalThis.LabHQState, api: {reportAsset() {}}, esc: String, fuLoading: new Set()});
  vm.runInContext(`${html.slice(start, end)}\nthis.render = reportHTML;`, context);
  assert.match(context.render({id: 'req_1', report: '![plot](steps/s/outputs/a.png)'}), /data-report-asset="steps\/s\/outputs\/a.png"/);
});

test('footnote labels agree across report and front without rewriting code', () => {
  const html = globalThis.LabHQState.reportMarkdownHTML('Result[^step_1].\n\nResult[^step_1].\n\n`[^step_1]`\n\n[^step_1]: Source text.');
  assert.equal((html.match(/Result\[근거 1\]/g) || []).length, 2);
  assert.match(html, /<li>Source text\.<\/li>/);
  assert.match(html, /<code>\[\^step_1\]<\/code>/);
});

test('footnote definitions render safe inline markup and authenticated assets', () => {
  const html = globalThis.LabHQState.reportMarkdownHTML(
    'Result[^s].\n\n[^s]: Step `analysis` · [Evidence](steps/s/outputs/evidence.md) · <script>alert(1)</script>', 'req_1');
  assert.match(html, /<code>analysis<\/code>/);
  assert.match(html, /data-report-asset="steps\/s\/outputs\/evidence.md"/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
});

test('stored source report receives the shared first screen at display time', () => {
  const packaged = globalThis.LabHQState.reportWithPackage('# 원본\n\n## 결과\n\n본문', {
    title: '원본', question: '질문', answer: '답', limitations: '한계',
    figures: [], tables: [], omissions: {figures: '그림 제외', tables: '표 제외'},
    reproduction: 'README.md', evidence_notes: ['s1/c1'], execution_record: 'execution_record.md',
  });
  const headings = [...packaged.matchAll(/^## (.+)$/gm)].map(match => match[1]);
  assert.deepEqual(headings.slice(0, 8), ['질문', '답', '한계', '대표 그림', '결과표', '재현 안내', '근거 각주', '실행 기록']);
  assert.match(packaged, /## 결과\n\n본문/);
  const html = globalThis.LabHQState.reportMarkdownHTML(packaged);
  assert.doesNotMatch(html, /labhq-report-package/);
  assert.match(html, /본문/);
  const code = globalThis.LabHQState.reportMarkdownHTML('```\n<!-- labhq-report-package:v1 -->\n```');
  assert.match(code, /&lt;!-- labhq-report-package:v1 --&gt;/);
  const indented = globalThis.LabHQState.reportMarkdownHTML('    <!-- labhq-report-package:v1 -->');
  assert.match(indented, /&lt;!-- labhq-report-package:v1 --&gt;/);
});
