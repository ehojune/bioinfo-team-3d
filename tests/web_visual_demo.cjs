// Visual regression for the 2D office demo (labhq/web/index.html?demo=1) in headless Chrome.
//
// It does not compare pixels. For each of four demo states (start, request running, decision waiting,
// report review) at desktop 1440x900 and phone 390x844 it records a layout snapshot of the main components
// (tabs, decision and review cards, task board, messenger, staff cards, office) -- bounding boxes,
// visibility and key computed colors -- and compares it with tests/visual/demo-layout.baseline.json
// within a small tolerance. A metrics-only font and a virtual page clock keep the result identical across
// operating systems and runs (see tests/support/). Skips, with a reason, when no Chrome is installed.
//
// Change the baseline on purpose (after an intended design change):
//   node scripts/update_visual_baseline.cjs        # or: LABHQ_UPDATE_VISUAL_BASELINE=1 node tests/web_visual_demo.cjs
// then review the printed differences and commit tests/visual/demo-layout.baseline.json.
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {openDemo, skipReason} = require('./support/demo_driver.cjs');
const {DEFAULT_TOLERANCE, snapshotSource, diffView, formatBaseline} = require('./support/layout_snapshot.cjs');

const BASELINE_PATH = path.join(__dirname, 'visual', 'demo-layout.baseline.json');
const UPDATE = process.env.LABHQ_UPDATE_VISUAL_BASELINE === '1' || process.argv.includes('--update');
const VIEWPORTS = {desktop: [1440, 900], phone: [390, 844]};
const TABS = ['decisions', 'tasks', 'feed', 'jobs'];
const HPC_PENDING = "[...__labhq.S.approvals.values()].some(a=>a.kind==='hpc_submit')";
const REQUEST_DONE = "[...__labhq.S.requests.values()].some(r=>r.status==='done')";
const WORKING = agent => `[...__labhq.S.activeTasks.values()].some(t=>t.agent_id==='${agent}')`;

async function showTab(d, tab) {
  await d.evaluate(`document.querySelector('[data-tab-target="${tab}"]').click()`);
  await d.frames();
}

async function snap(d, view) {
  await d.evaluate('window.scrollTo(0, 0)');
  await d.frames();
  return d.evaluate(snapshotSource(view));
}

// The default view is the decisions tab; the task board and messenger need their own tab, and on a phone
// the staff strip is collapsed until its toggle is pressed.
async function captureViews(d) {
  const views = {default: await snap(d, 'default')};
  for (const tab of ['tasks', 'feed']) {
    await showTab(d, tab);
    views[tab] = await snap(d, tab);
  }
  await showTab(d, 'decisions');
  const toggle = "(() => { const t = document.querySelector('#staff-toggle'); return t && getComputedStyle(t).display !== 'none' ? t : null; })()";
  const collapsible = await d.evaluate(`Boolean(${toggle})`);
  if (collapsible) { await d.evaluate(`${toggle}.click()`); await d.frames(); }
  views.staff = await snap(d, 'staff');
  if (collapsible) { await d.evaluate(`${toggle}.click()`); await d.frames(); }
  return views;
}

const first = (view, group, key) => (view.items[group] || []).find(r => r.key === key);

// Facts that must hold in every run, whatever the baseline says.
function assertState(name, label, views) {
  const where = `${label}/${name}`;
  for (const [view, snap] of Object.entries(views)) {
    assert.ok(snap.doc.w <= snap.viewport.w, `${where}/${view}: horizontal overflow (${snap.doc.w} > ${snap.viewport.w})`);
  }
  const main = views.default, items = main.items;
  assert.deepEqual(items.tab.map(t => t.key), TABS, `${where}: tab order`);
  assert.equal(first(main, 'tab', 'decisions').status, 'true', `${where}: decisions tab is selected first`);
  assert.ok(items.tab.every(t => t.shown), `${where}: all four tabs visible`);
  assert.equal(items.character.length, 11, `${where}: full roster artwork`);
  assert.equal(items.seat.length, 11, `${where}: eleven office seats`);
  assert.ok(items.seat.every(s => s.shown) && items.character.every(c => c.shown), `${where}: office seats and characters visible`);
  assert.equal(views.staff.items['staff-card'].length, 11, `${where}: eleven staff cards`);
  assert.equal(items['seat-status'].length, 11, `${where}: a status line per seat`);
  assert.ok(first(main, 'wall-board', 'wall-board').shown, `${where}: wall board visible`);
  assert.ok(views.staff.items['staff-card'].every(c => c.shown), `${where}: staff cards visible once the strip is open`);
  const decisions = items.decision.filter(r => r.shown), reviews = items.review.filter(r => r.shown), count = items['tab-count'][0];
  if (name === 'start') {
    assert.equal(decisions.length, 0, `${where}: no decision cards yet`);
    assert.equal(count.shown, false, `${where}: no count badge without decisions`);
    assert.ok(items.seat.every(s => s.status === 'idle'), `${where}: everyone idle`);
  } else {
    assert.ok(decisions.length + reviews.length >= 1, `${where}: a decision card shows`);
    assert.equal(count.shown, true, `${where}: count badge shows`);
    assert.equal(count.text, String(decisions.length + reviews.length), `${where}: badge counts the cards`);
  }
  if (name === 'running') assert.equal(first(main, 'seat', 'biologist').status, 'working', `${where}: biologist working`);
  if (name === 'decision') {
    assert.equal(first(main, 'seat', 'data_steward').status, 'decision', `${where}: data steward waits for the PI`);
    assert.ok(decisions.some(r => r.key.startsWith('approval:')), `${where}: the HPC approval card shows`);
    assert.ok(items['decision-primary'].some(b => b.shown && b.text === '승인'), `${where}: approve button visible`);
  }
  if (name === 'report') {
    assert.ok(reviews.length >= 1, `${where}: report review card shows`);
    assert.ok(items['review-primary'].some(b => b.shown && b.text === '보고서 열기'), `${where}: open-report button visible`);
    assert.equal(first(main, 'seat', 'cso').status, 'done', `${where}: CSO finished`);
  }
  assert.equal(views.tasks.items['task-board'][0].shown, true, `${where}: task board visible on its tab`);
  assert.equal(views.feed.items.messenger[0].shown, true, `${where}: messenger visible on its tab`);
}

async function runViewport(label, [width, height]) {
  const d = await openDemo({width, height, reducedMotion: true, layoutFont: true});
  const captured = {};
  try {
    await d.load();
    captured.start = await captureViews(d);
    await d.until('request running', WORKING('biologist'));
    captured.running = await captureViews(d);
    await d.until('decision waiting', HPC_PENDING);
    captured.decision = await captureViews(d);
    // Approve through the real button, as the PI would.
    await d.evaluate("document.querySelector('#approvals .decision-card[data-kind=\"hpc_submit\"] .decision-primary').click()");
    await d.frames();
    assert.equal(await d.evaluate(HPC_PENDING), false, `${label}: the approve button resolves the HPC approval`);
    await d.until('report review', REQUEST_DONE, {maxVirtualMs: 200000});
    captured.report = await captureViews(d);
    assert.equal(await d.evaluate("document.getAnimations().length"), 0, `${label}: reduced motion leaves no running animation`);
    assert.deepEqual(d.errors, [], `${label}: page errors`);
    assert.deepEqual(d.external, [], `${label}: external requests`);
  } finally {
    await d.close();
  }
  return captured;
}

const reason = skipReason();
test('2D demo layout matches the committed snapshot at desktop and phone sizes',
  {skip: reason || false, timeout: 240000}, async () => {
    const actual = {};
    for (const [label, size] of Object.entries(VIEWPORTS)) {
      const states = await runViewport(label, size);
      for (const [name, views] of Object.entries(states)) {
        assertState(name, label, views);
        for (const [view, snap] of Object.entries(views)) actual[`${label}/${name}/${view}`] = snap;
      }
    }
    const old = fs.existsSync(BASELINE_PATH) ? JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf8')) : null;
    if (UPDATE) {
      const data = {version: 1, note: 'Layout snapshot of labhq/web/index.html?demo=1 with the metrics-only test font. Regenerate with node scripts/update_visual_baseline.cjs.',
        tolerance: DEFAULT_TOLERANCE, views: actual};
      fs.mkdirSync(path.dirname(BASELINE_PATH), {recursive: true});
      fs.writeFileSync(BASELINE_PATH, formatBaseline(data));
      const changed = old ? Object.keys(actual).flatMap(key => (old.views[key] ? diffView(key, old.views[key], actual[key], old.tolerance) : [`${key}: new view`])) : [];
      console.log(`baseline written: ${Object.keys(actual).length} views` + (old ? `, ${changed.length} differences from the previous baseline` : ''));
      for (const line of changed.slice(0, 60)) console.log('  ' + line);
      if (changed.length > 60) console.log(`  ... ${changed.length - 60} more`);
      return;
    }
    assert.ok(old, `no baseline at ${path.relative(process.cwd(), BASELINE_PATH)}; run node scripts/update_visual_baseline.cjs`);
    assert.deepEqual(Object.keys(old.views).sort(), Object.keys(actual).sort(), 'baseline and run cover the same views');
    const diffs = Object.keys(actual).flatMap(key => diffView(key, old.views[key], actual[key], {...DEFAULT_TOLERANCE, ...old.tolerance}));
    assert.equal(diffs.length, 0, `layout differs from the baseline (${diffs.length}); if intended, run node scripts/update_visual_baseline.cjs\n  ` + diffs.slice(0, 40).join('\n  '));
    console.log(`visual demo layout: ${Object.keys(actual).length} views match the baseline`);
  });
