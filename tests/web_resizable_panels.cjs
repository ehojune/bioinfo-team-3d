// #833: this process's monotonic clock at the first line of the file, before any require (see `timing` below).
const MODULE_ENTRY_MONO_MS = performance.now();
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {openDemo, skipReason, CLOSE_RESERVE_MS} = require('./support/demo_driver.cjs');

const web = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../labhq/web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const components = fs.readFileSync(path.join(web, 'ui/components.css'), 'utf8');
const tokens = fs.readFileSync(path.join(web, 'ui/tokens.css'), 'utf8');
const load = file => import('data:text/javascript;base64,' + fs.readFileSync(path.join(web, 'ui', file)).toString('base64'));

test('panel resize values are bounded, persisted defensively and keyboard-addressable', async () => {
  const resize = await load('resizable.js');
  assert.deepEqual(resize.readPanelSizes('{"main":640,"staff":144}'), {main:640, staff:144});
  assert.deepEqual(resize.readPanelSizes('{"main":"wide","staff":-2}'), {});
  assert.deepEqual(resize.readPanelSizes('not json'), {});
  assert.equal(resize.clampPanelSize(900, 320, 760), 760);
  assert.equal(resize.clampPanelSize(10, 92, 280), 92);
  assert.equal(resize.keyDelta('vertical', 'ArrowLeft'), -resize.RESIZE_STEP);
  assert.equal(resize.keyDelta('vertical', 'ArrowRight'), resize.RESIZE_STEP);
  assert.equal(resize.keyDelta('horizontal', 'ArrowUp'), resize.RESIZE_STEP);
  assert.equal(resize.keyDelta('horizontal', 'ArrowDown'), -resize.RESIZE_STEP);
  assert.equal(resize.keyDelta('vertical', 'ArrowUp'), 0);
});

test('2.5D shell exposes two token-styled accessible splitters and leaves phone layout unchanged', () => {
  assert.match(html, /id="main-splitter"[^>]+role="separator"[^>]+aria-orientation="vertical"/);
  assert.match(html, /id="staff-splitter"[^>]+role="separator"[^>]+aria-orientation="horizontal"/);
  assert.match(html, /import \{initResizablePanels\} from '\.\/ui\/resizable\.js'/);
  assert.match(html, /storageKey:\s*`panel_sizes:\$\{INSTANCE\}`/);
  assert.match(components, /\.panel-splitter--vertical[^}]*cursor:\s*col-resize/);
  assert.match(components, /\.panel-splitter--horizontal[^}]*cursor:\s*row-resize/);
  assert.match(components, /\.panel-splitter:focus-visible[^}]*--ds-accent/);
  assert.match(tokens, /--ds-resize-hit:\s*18px/);
  assert.match(components, /@media \(max-width:\s*620px\)[\s\S]*\.panel-splitter[^}]*display:\s*none/);
});

// #833: tests/test_web_3d.py runs each viewport as its own invocation (LABHQ_RESIZABLE_VIEWPORT=desktop or phone), so
// each gets its own 30 s watchdog; one shared budget left the second viewport no time (job 120174823730). Only the
// selected browser test is registered. Run directly without the variable, both run here one after the other.
const VIEWPORTS = ['desktop', 'phone'];
const selected = process.env.LABHQ_RESIZABLE_VIEWPORT;
if (selected !== undefined && !VIEWPORTS.includes(selected)) {
  throw new Error(`LABHQ_RESIZABLE_VIEWPORT must be one of: ${VIEWPORTS.join(', ')}`);
}
// The watchdog kills this script at LABHQ_TEST_OUTER_DEADLINE_MS (epoch ms), which skips every close below. The work
// deadline ends the browser work CLOSE_RESERVE_MS before it, so the open session's own close and this process's exit
// still fit. It is shared by the stages of one viewport only: an outer deadline without a selected viewport is refused.
const outerDeadline = Number(process.env.LABHQ_TEST_OUTER_DEADLINE_MS);
const workDeadline = Number.isFinite(outerDeadline) && outerDeadline > 0 ? outerDeadline - CLOSE_RESERVE_MS : undefined;
// The required CI case gives each viewport a real cold-browser start (up to 30 s) and UI phase (up to 20 s).
// Both stay inside the same 60 s Python watchdog, including Node startup and the owned close reserve.
const STARTUP_BUDGET_MS = 30000, UI_BUDGET_MS = 20000;
const phaseBudgets = workDeadline === undefined ? {} : {startupBudgetMs: STARTUP_BUDGET_MS, uiBudgetMs: UI_BUDGET_MS};
if (process.env.LABHQ_TEST_OUTER_DEADLINE_MS !== undefined && selected === undefined) {
  throw new Error('LABHQ_TEST_OUTER_DEADLINE_MS needs LABHQ_RESIZABLE_VIEWPORT: one deadline per viewport invocation');
}
if (process.env.LABHQ_REQUIRE_RESIZABLE_BROWSER === '1' && workDeadline === undefined) {
  throw new Error('required resizable browser needs a finite outer deadline');
}
const runs = viewport => selected === undefined || selected === viewport;

// #833: where the time before the driver's own trace goes (Node start, module load, the static tests). Fixed numeric
// rows only, no path, URL or profile. Two clocks, never mixed: `*_mono_ms` is this process's monotonic clock since its
// start (performance.timeOrigin); `*_epoch_ms` and `*_remaining_ms` are wall clock, the clock of the watchdog's
// LABHQ_TEST_OUTER_DEADLINE_MS. Observation only: no budget is reset or extended here.
function timing(stage, extra = {}) {
  const epoch = Date.now();
  console.error('resizable timing: ' + JSON.stringify({stage, ...extra,
    time_origin_epoch_ms: Math.round(performance.timeOrigin), mono_ms: Math.round(performance.now()), epoch_ms: epoch,
    outer_remaining_ms: workDeadline === undefined ? null : outerDeadline - epoch,
    work_remaining_ms: workDeadline === undefined ? null : workDeadline - epoch}));
}
timing('module-ready', {module_entry_mono_ms: Math.round(MODULE_ENTRY_MONO_MS)});

// The critical Windows phase requires a real browser, never an optional skip.
if (process.env.LABHQ_REQUIRE_RESIZABLE_BROWSER === '1' && skipReason()) {
  throw new Error(`required resizable browser unavailable: ${skipReason()}`);
}
if (runs('desktop')) test('desktop splitters drag, respond to arrows, persist and reset',
  {skip: skipReason() || false, timeout: 60000}, async () => {
    console.error('resizable browser: desktop open');
    timing('open', {viewport: 'desktop'});
    const desktop = await openDemo({width: 1440, height: 900, reducedMotion: true, workDeadline, ...phaseBudgets,
      diagnostic: event => console.error('resizable driver: ' + JSON.stringify({viewport: 'desktop', ...event}))});
    try {
      console.error('resizable browser: desktop load');
      await desktop.load();
      console.error('resizable browser: desktop assertions');
      const before = await desktop.evaluate("document.querySelector('#office').getBoundingClientRect().width");
      const box = await desktop.evaluate("(()=>{const b=document.querySelector('#main-splitter').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()");
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mousePressed', x:box.x, y:box.y, button:'left', buttons:1, clickCount:1});
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mouseMoved', x:box.x + 80, y:box.y, button:'left', buttons:1});
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mouseReleased', x:box.x + 80, y:box.y, button:'left', buttons:0, clickCount:1});
      await desktop.frames();
      const dragged = await desktop.evaluate("document.querySelector('#office').getBoundingClientRect().width");
      assert.ok(dragged >= before + 75, `${before} -> ${dragged}`);
      await desktop.evaluate("document.querySelector('#main-splitter').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))");
      await desktop.frames();
      const keyed = await desktop.evaluate("document.querySelector('#office').getBoundingClientRect().width");
      assert.ok(Math.abs(keyed - (dragged - 24)) <= 1, `${dragged} -> ${keyed}`);
      const staffBefore = await desktop.evaluate("document.querySelector('.staff-wrap').getBoundingClientRect().height");
      const staffBox = await desktop.evaluate("(()=>{const b=document.querySelector('#staff-splitter').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}})()");
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mousePressed', x:staffBox.x, y:staffBox.y, button:'left', buttons:1, clickCount:1});
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mouseMoved', x:staffBox.x, y:staffBox.y - 40, button:'left', buttons:1});
      await desktop.cdp('Input.dispatchMouseEvent', {type:'mouseReleased', x:staffBox.x, y:staffBox.y - 40, button:'left', buttons:0, clickCount:1});
      await desktop.frames();
      const staffDragged = await desktop.evaluate("document.querySelector('.staff-wrap').getBoundingClientRect().height");
      assert.ok(staffDragged >= staffBefore + 35, `${staffBefore} -> ${staffDragged}`);
      assert.match(await desktop.evaluate("localStorage.getItem('labhq_demo:panel_sizes:')"), /"main":/);
      assert.match(await desktop.evaluate("localStorage.getItem('labhq_demo:panel_sizes:')"), /"staff":/);
      await desktop.evaluate("document.querySelector('#main-splitter').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
      await desktop.evaluate("document.querySelector('#staff-splitter').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
      await desktop.frames();
      assert.equal(await desktop.evaluate("localStorage.getItem('labhq_demo:panel_sizes:')"), '{}');
    } finally {
      console.error('resizable browser: desktop close');
      await desktop.close();
      console.error('resizable browser: desktop closed');
    }
  });

if (runs('phone')) test('phone layout hides the splitters and keeps the office full width',
  {skip: skipReason() || false, timeout: 60000}, async () => {
    console.error('resizable browser: phone open');
    timing('open', {viewport: 'phone'});
    const phone = await openDemo({width: 390, height: 844, reducedMotion: true, workDeadline, ...phaseBudgets,
      diagnostic: event => console.error('resizable driver: ' + JSON.stringify({viewport: 'phone', ...event}))});
    try {
      console.error('resizable browser: phone load');
      await phone.load();
      console.error('resizable browser: phone assertions');
      assert.equal(await phone.evaluate("getComputedStyle(document.querySelector('#main-splitter')).display"), 'none');
      const widths = await phone.evaluate("(()=>{const l=document.querySelector('.layout'),s=getComputedStyle(l);return [l.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight),document.querySelector('#office').clientWidth]})()");
      assert.ok(Math.abs(widths[0] - widths[1]) <= 2, widths.join(' vs '));
    } finally {
      console.error('resizable browser: phone close');
      await phone.close();
      console.error('resizable browser: phone closed');
    }
  });

