const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const web = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(web, 'ui/components.css'), 'utf8');
const load = file => import('data:text/javascript;base64,' + fs.readFileSync(path.join(web, 'ui', file)).toString('base64'));
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this._text = ''; this.value = ''; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(k,v) { this.attributes[k] = v; }
  removeChild(child) { this.children = this.children.filter(c => c !== child); }
  insertBefore(child, ref) { this.removeChild(child); this.children.splice(this.children.indexOf(ref), 0, child); }
}
global.document = {createElement: tag => new Element(tag)};
const nodes = e => [e, ...e.children.flatMap(nodes)];
const classes = (e, c) => String(e.className || '').split(' ').includes(c);
const mod = load('decide.js');
const approval = {id:'hpc', kind:'hpc_submit', summary:'HPC submit command', pi_summary:'자료를 받아 확인하려 합니다.',
  pi_reason:'분석에 쓸 자료가 필요합니다.', pi_outcome:'승인하면 제출합니다.', detail:{cores:4, mem:'32G', walltime:'06:00:00', queue:'batch', reason:'자료 확인', command:'python check.py'}};
async function card(value = approval, options = {}) {
  const root = new Element();
  return (await mod).syncDecisionCards(root, [value], [], {presentation:'journal', ...options})[0];
}
test('decision badge updates, hides at zero, includes review but not operational holds', async () => {
  const {updateDecisionBadge} = await load('shell.js'), badge = new Element();
  updateDecisionBadge(badge, 3); assert.equal(badge.textContent, '3'); assert.equal(badge.hidden, false);
  assert.equal(badge.attributes['aria-label'], '결정 대기 3건');
  updateDecisionBadge(badge, 0); assert.equal(badge.hidden, true); assert.equal(badge.textContent, '');
  assert.match(html, /const decisions = items.length \+ sugs.length \+ reviews.length;/);
  assert.match(html, /updateDecisionBadge\(\$\('#decision-count'\), decisions\)/);
});
test('card order is question, facts, explanation, full width 48px action, memo, closed original', async () => {
  const row = await card(), p = row._decisionParts, all = nodes(row);
  assert.match(p.heading?.textContent || '', /\?$/);
  const ordered = [p.heading,p.facts,p.summary,p.reason,p.approve,p.note,p.detail].map(e=>all.indexOf(e));
  assert.ok(ordered.every((v,i)=>v>=0 && (!i || v>ordered[i-1])));
  assert.ok(classes(p.approve, 'decision-primary')); assert.ok(classes(p.deny, 'decision-secondary'));
  assert.match(css, /\.decision-primary\.ui-button\s*\{[^}]*width:\s*100%[^}]*min-height:\s*var\(--ds-h-touch\)/);
  const fold = all.find(e => classes(e, 'decision-source'));
  assert.equal(Boolean(fold.open), false); assert.equal(fold.children[0].textContent, '자세히');
  assert.match(fold.textContent, /python check\.py/);
  assert.equal(nodes(fold).filter(e=>e.tagName==='details').length,1, 'one disclosure opens the original command');
});
test('facts preserve supplied resources, money and missing values', async () => {
  const {decisionFacts} = await mod;
  assert.deepEqual(decisionFacts(approval), [['자원','4코어 · 32G'], ['최대 시간','06:00:00'], ['대기열','batch']]);
  assert.deepEqual(decisionFacts({kind:'budget',detail:{spent_usd:0,limit_usd:10,requested_budget_usd:20}}), [['사용 비용','$0.00'],['현재 상한','$10.00'],['새 상한','$20.00']]);
  assert.equal(decisionFacts({kind:'hpc_submit',summary:'16 cores'})[0][1], '미제공');
});
test('2D refresh preserves memo, open source and exact decision payload', async () => {
  const {syncDecisionCards} = await mod, root = new Element(), calls = [];
  const options = {presentation:'journal',onDecision:(...args)=>calls.push(args)};
  const [row] = syncDecisionCards(root,[approval],[],options), p = row._decisionParts;
  const source = nodes(row).find(e=>classes(e,'decision-source'));
  source.open = true; p.note.value = '확인했습니다';
  assert.equal(syncDecisionCards(root,[{...approval}],[],options)[0],row);
  assert.equal(source.open,true); assert.equal(p.note.value,'확인했습니다');
  p.approve.onclick(); assert.equal(calls[0][0].id,'hpc'); assert.equal(calls[0][1],true); assert.equal(calls[0][2],'확인했습니다');
  p.deny.onclick(); assert.equal(calls[1][1],false); assert.equal(calls[1][2],'확인했습니다');
});
test('all card kinds have Korean field captions, including unknown fields', async () => {
  for (const kind of ['hpc_submit','tool_permission','budget','recruit','research_plan','research_evidence','research_continue','resume','question','clarify','scope','facilities_fix','result_storage','result_publish','future_kind']) {
    const row = await card({...approval, kind, detail:{reason:'이유', detail:'상세', unknown_field:'원본 값', command:'python check.py'}});
    assert.ok(classes(row, 'decision-card'));
    const captions = nodes(row).filter(e => e.tagName === 'dt');
    assert.ok(captions.length >= 2);
    for (const caption of captions) assert.match(caption.textContent, /[가-힣]/, `${kind}: ${caption.textContent}`);
  }
});
test('status combines dot and text; login has separate shape and no primary action', async () => {
  const row = await card(), status = nodes(row).find(e => classes(e,'decision-status'));
  assert.match(status?.textContent || '', /결정 대기/);
  assert.ok(status.children.some(e => classes(e,'status-dot') && e.attributes['aria-hidden'] === 'true'));
  assert.match(css, /\.status-dot\s*\{[^}]*border-radius:\s*50%/);
  assert.match(css, /\.ap\.operation-card\s*\{[^}]*dashed/);
  const holds = html.split('const holdRows = holds.map')[1].split('const holdList')[0];
  assert.match(holds, /운영 문제/); assert.doesNotMatch(holds, /btn go|ui-button--primary/);
});
test('phone shows decisions before office and brings selected tab into view', () => {
  assert.equal([...html.matchAll(/data-tab-target="([^"]+)"/g)][0][1], 'decisions');
  assert.match(css, /@media\s*\(max-width:\s*620px\)\s*\{\s*\.layout\s*\{[^}]*flex-direction:\s*column/);
  assert.match(css, /#command-center\s*\{[^}]*order:\s*-1/);
  assert.match(html, /\$\('#command-center'\)\.scrollIntoView/);
});
test('review/history retain actions, report, time and hide in shared cards; 3D default stays unchanged', async () => {
  const {syncReviewCards,syncDecisionHistory} = await mod;
  require('../labhq/web/state.js');
  const reviews = new Element(), history = new Element(), calls = [];
  const [review] = syncReviewCards(reviews,[{id:'r',status:'done',text:'분석 결과',pi_review_pending:true}],{presentation:'journal',onDone:id=>calls.push(id)});
  assert.ok(classes(review,'ui-card'));
  nodes(review).find(n => n.tagName === 'button' && n.textContent === '검토 완료').onclick();
  assert.deepEqual(calls,['r']);
  syncDecisionHistory(history,[{state:'resolved',approved:true,decided_at:1234,approval:{...approval,request_id:'r'}}],
    {presentation:'journal',requests:new Map([['r',{text:'요청',report:'보고서'}]]),visibility:{has:()=>false,hide:()=>{}}});
  assert.ok(nodes(history).some(n=>classes(n,'ui-list-row')));
  assert.ok(nodes(history).some(n=>n.tagName==='time' && n.dateTime));
  assert.ok(nodes(history).some(n=>n.tagName==='a' && n.textContent==='요청 보고서'));
  assert.ok(nodes(history).some(n=>n.tagName==='button' && n.textContent==='×'));
  assert.equal((await card(approval, {presentation:undefined}))._decisionParts.heading, undefined);
});
