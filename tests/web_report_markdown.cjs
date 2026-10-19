const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

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

test('stored source report receives the shared first screen at display time', () => {
  const packaged = globalThis.LabHQState.reportWithPackage('# 원본\n\n## 결과\n\n본문', {
    title: '원본', question: '질문', answer: '답', limitations: '한계',
    figures: [], tables: [], omissions: {figures: '그림 제외', tables: '표 제외'},
    reproduction: 'README.md', evidence_notes: ['s1/c1'], execution_record: 'execution_record.md',
  });
  const headings = [...packaged.matchAll(/^## (.+)$/gm)].map(match => match[1]);
  assert.deepEqual(headings.slice(0, 8), ['질문', '답', '한계', '대표 그림', '결과표', '재현 안내', '근거 각주', '실행 기록']);
  assert.match(packaged, /## 결과\n\n본문/);
});
