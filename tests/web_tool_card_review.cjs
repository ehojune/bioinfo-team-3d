const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');
const path = require('node:path');
const root = path.resolve(__dirname, '../labhq/web');
test('staff activity summarizes scripts and retains the received command', () => {
  const ctx = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(root, 'state.js'), 'utf8'), ctx);
  const office = ctx.LabHQState.createOfficeState();
  office.apply({type:'snapshot', data:{agents:[{id:'recruiter'}]}});
  const raw = 'P=.tmp/p2s-scripts/paper_bundle.py uv run "$P" build --work .tmp/paper-review';
  office.apply({type:'agent.tool', agent_id:'recruiter', data:{name:'Bash', input:raw, input_truncated:true, input_limit:400}});
  const row = office.ag('recruiter').log.at(-1);
  assert.equal(row.piText, '논문 묶음을 만드는 스크립트 실행(uv)');
  assert.ok(row.fullText.includes(raw));
  assert.equal(office.toolActivitySummary({name:'Bash', input:raw}), row.piText);
  assert.ok(!Object.keys(row).includes('piText'), 'display copy must not change replay state');
  assert.equal(office.toolActivitySummary({name:'Bash', input:'python opaque.py'}), '스크립트 실행(python)');
});
test('PI detail toggles hide internal truncation captions', async () => {
  class Element {
    constructor(tag) { this.tagName=tag; this.children=[]; this.textContent=''; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute() {}
  }
  global.document={createElement:tag=>new Element(tag)};
  const source=fs.readFileSync(path.join(root, 'ui/decide.js'),'utf8');
  const {expandableText}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const parent=new Element('div');
  expandableText(parent, 'uv run script.py', {fold:true,summary:'자세히',truncated:true,cutChars:400});
  const texts=n=>[n.textContent,...n.children.flatMap(texts)].join(' ');
  assert.ok(!texts(parent).includes('서버에서'));
  assert.ok(texts(parent).includes('uv run script.py'));
});
