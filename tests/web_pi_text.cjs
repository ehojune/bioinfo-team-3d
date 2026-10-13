const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const root = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const source = fs.readFileSync(path.join(root, 'ui/decide.js'), 'utf8');
const mod = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this._text=''; }
  set textContent(v) { this._text=String(v); this.children=[]; }
  get textContent() { return this._text+this.children.map(c=>c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text=''; this.children=children; }
  setAttribute(k,v) { this.attributes[k]=v; }
}
global.document={createElement:tag=>new Element(tag)};
const nodes=e=>[e,...e.children.flatMap(nodes)];
test('PI prose removes headings, emphasis and table syntax; keeps gene identifiers', async()=>{
  const {plainPiText}=await mod;
  assert.equal(plainPiText('# **결론**\n- `TP53` 확인\n- BRCA_1 유지'), '결론\nTP53 확인\nBRCA_1 유지');
  assert.equal(plainPiText('| 항목 | 결과 |\n| --- | --- |\n| 표본 | 2 |'), '항목 · 결과\n표본 · 2');
});
test('messenger previews preserve list line breaks in both offices',async()=>{
  const {piText}=await mod, parent=new Element();
  piText(parent,'# 결과\n- 첫 표\n- 두 번째 표',{limit:150});
  assert.equal(parent.children[0].textContent,'결과\n첫 표\n두 번째 표');
  assert.equal(parent.children[0].className,'pi-prose');
  for (const file of ['index.html','lab3d/index.html'])
    assert.match(fs.readFileSync(path.join(root,file),'utf8'),/\.pi-prose\{[^}]*white-space:pre-wrap/);
});
test('messenger code remains exact only inside closed details with Korean label', async()=>{
  const {piText}=await mod, parent=new Element();
  const raw='## **확인**\n```python\nprint("<unsafe>")\n```';
  piText(parent, raw);
  const fold=nodes(parent).find(n=>n.tagName==='details');
  assert.equal(fold.open,false);
  assert.equal(fold.children[0].textContent,'자세히');
  assert.ok(!parent.children.filter(n=>n!==fold).map(n=>n.textContent).join('').includes('print('));
  assert.equal(nodes(fold).find(n=>n.tagName==='pre').textContent,raw);
});
test('card uses Korean explanation and puts even short raw commands in details',async()=>{
  const {displaySummary,renderDetail}=await mod;
  const a={kind:'tool_permission',summary:'PowerShell: Remove-Item',pi_summary:'임시 폴더를 정리하려 합니다.',pi_reason:'다시 계산할 공간이 필요합니다.',pi_outcome:'승인하면 삭제합니다.',detail:{input:'Remove-Item .tmp -Recurse'}};
  assert.equal(displaySummary(a),a.pi_summary);
  const parent=new Element(); renderDetail(parent,a.kind,a.detail,a);
  const fold=nodes(parent).find(n=>n.tagName==='details');
  assert.equal(fold.open,false);
  assert.ok(!parent.children.filter(n=>n!==fold).map(n=>n.textContent).join('').includes('Remove-Item'));
  assert.ok(parent.textContent.includes(a.pi_reason));
});
test('English engine text gets an honest Korean notice and preserves source',async()=>{
  const {piText}=await mod, parent=new Element(); piText(parent,'Permission denied by engine');
  assert.match(parent.children[0].textContent,/[가-힣]/);
  assert.ok(nodes(parent).find(n=>n.tagName==='pre').textContent.includes('Permission denied'));
});
test('unfenced code with Korean literals stays in details',async()=>{
  const {piText}=await mod, parent=new Element(); piText(parent,'확인했습니다.\nprint("비교 결과")');
  assert.equal(parent.children[0].textContent,'확인했습니다.');
  assert.ok(nodes(parent).find(n=>n.tagName==='pre').textContent.includes('print('));
});
test('a summary kept open survives unchanged card refresh',async()=>{
  const {syncDecisionCards}=await mod, parent=new Element();
  const card={id:'question',kind:'clarify',summary:'English source'};
  const [row]=syncDecisionCards(parent,[card]);
  const fold=nodes(row._decisionParts.summary).find(n=>n.tagName==='details'); fold.open=true;
  syncDecisionCards(parent,[{...card}]);
  assert.equal(nodes(row._decisionParts.summary).find(n=>n.tagName==='details'),fold);
  assert.equal(fold.open,true);
});
