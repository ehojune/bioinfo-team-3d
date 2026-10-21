const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {openDemo, skipReason} = require('./support/demo_driver.cjs');

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

test('desktop splitter drags, responds to arrows, persists and resets while phone hides it',
  {skip: skipReason() || false, timeout: 60000}, async () => {
    console.error('resizable browser: desktop open');
    const desktop = await openDemo({width: 1440, height: 900, reducedMotion: true});
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

    console.error('resizable browser: phone open');
    const phone = await openDemo({width: 390, height: 844, reducedMotion: true});
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

