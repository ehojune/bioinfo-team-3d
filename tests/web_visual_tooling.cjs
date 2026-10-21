// Checks for the visual-test tooling itself (#546, remaining review notes of PR #599): the layout snapshot's color
// comparison, the baseline updater's exit status, the virtual page clock, and what the driver reports. No Chrome is
// needed except where a test says so.
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {DEFAULT_TOLERANCE, diffView, sameColor, colorChannels} = require('./support/layout_snapshot.cjs');
const {updateBaseline} = require('../scripts/update_visual_baseline.cjs');
const vm = require('node:vm');
const {openDemo, skipReason, CLOCK_SOURCE, closeDemoResources} = require('./support/demo_driver.cjs');

// ---- layout_snapshot: colors are #rrggbb hex bytes ----
test('sameColor reads #rrggbb as one hex byte per channel', () => {
  assert.deepEqual(colorChannels('#1d6b40'), [0x1d, 0x6b, 0x40, 1]);
  assert.deepEqual(colorChannels('#505b67'), [0x50, 0x5b, 0x67, 1]);
  // green 0x6b vs 0x7b is 16 apart: a real regression the tolerance of 2 must not hide
  assert.equal(sameColor('#1d6b40', '#1e7b40', DEFAULT_TOLERANCE), false);
  // red 0x50 vs 0x51 is 1 apart: within tolerance
  assert.equal(sameColor('#505b67', '#515b67', DEFAULT_TOLERANCE), true);
});

test('the tolerance is in channel values, and #rrggbbaa alpha is a byte', () => {
  const tol = {colorChannel: 2};
  assert.equal(sameColor('#101010', '#121212', tol), true);   // exactly 2
  assert.equal(sameColor('#101010', '#131010', tol), false);  // 3
  assert.equal(sameColor('#1d6b40', '#1d6b40', tol), true);
  assert.equal(sameColor('#1d6b4080', '#1d6b4080', tol), true);
  assert.equal(sameColor('#1d6b4080', '#1d6b4081', tol), true);   // one alpha step is 0.4%
  assert.equal(sameColor('#1d6b4080', '#1d6b40', tol), false);    // half transparent vs opaque
  assert.equal(sameColor('#1d6b40', undefined, tol), false);
  assert.equal(sameColor(undefined, undefined, tol), true);
  assert.equal(sameColor('#1d6b40', 'rebeccapurple', tol), false);
});

test('decimal rgb() text still compares channel by channel', () => {
  const tol = {colorChannel: 2};
  assert.equal(sameColor('rgb(29, 107, 64)', 'rgb(30, 107, 64)', tol), true);
  assert.equal(sameColor('rgb(29, 107, 64)', 'rgb(30, 123, 64)', tol), false);
  assert.equal(sameColor('rgba(29, 107, 64, 0.5)', 'rgba(29, 107, 64, 1)', tol), false);
});

test('diffView reports a color that moved by more than the tolerance and not one inside it', () => {
  const view = color => ({viewport: {w: 100, h: 100}, doc: {w: 100, h: 100},
    items: {tab: [{key: 'decisions', shown: true, paint: {color, background: '#ffffff'}}]}});
  assert.deepEqual(diffView('desktop', view('#505b67'), view('#515b67')), []);
  const diffs = diffView('desktop', view('#1d6b40'), view('#1e7b40'));
  assert.equal(diffs.length, 1);
  assert.match(diffs[0], /tab\[decisions\] paint\.color #1d6b40 -> #1e7b40/);
});

// ---- scripts/update_visual_baseline.cjs: its exit status says whether a baseline was written ----
// `initial` is the baseline's content before the run (null: no file). `child` plays the visual test.
function updater({skip = '', initial = '{"v":1}', child}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'labhq-baseline-test-'));
  const baseline = path.join(dir, 'baseline.json');
  if (initial !== null) fs.writeFileSync(baseline, initial);
  const out = {spawned: 0, log: [], error: []};
  try {
    out.status = updateBaseline({baseline, skip: () => skip, log: m => out.log.push(m), error: m => out.error.push(m),
      run: () => { out.spawned += 1; return child(baseline); }});
  } finally { fs.rmSync(dir, {recursive: true, force: true}); }
  return out;
}
const rewrite = (content, status = 0) => file => { fs.writeFileSync(file, content); return {status}; };

test('a skipped visual test (no Chrome) fails the update, and the test is not even started', () => {
  const r = updater({skip: 'no Chrome found (set LABHQ_TEST_CHROME)', child: rewrite('{"v":2}')});
  assert.equal(r.status, 1);
  assert.equal(r.spawned, 0);
  assert.match(r.error.join('\n'), /NOT updated.*skipped.*no Chrome found/);
  assert.deepEqual(r.log, []);
});

test('a child that exits 0 and leaves the baseline as it was fails the update', () => {
  for (const child of [() => ({status: 0}), rewrite('{"v":1}')]) {  // never wrote it / wrote the same bytes
    const r = updater({child});
    assert.equal(r.status, 1);
    assert.match(r.error.join('\n'), /NOT updated.*unchanged.*nothing to commit/);
    assert.deepEqual(r.log, [], 'no commit hint for an unchanged baseline');
  }
  const missing = updater({initial: null, child: () => ({status: 0})});  // skipped, and there never was a file
  assert.equal(missing.status, 1);
});

test('only a change of line endings (a CRLF checkout) is not a changed baseline', () => {
  const r = updater({initial: '{"v":1}\r\n{"w":2}\r\n', child: rewrite('{"v":1}\n{"w":2}\n')});
  assert.equal(r.status, 1);
  assert.match(r.error.join('\n'), /unchanged/);
});

test('a rewritten baseline exits 0 and points at the review step', () => {
  for (const initial of ['{"v":1}', null]) {  // changed, or created
    const r = updater({initial, child: rewrite('{"v":2}')});
    assert.equal(r.status, 0);
    assert.match(r.log.join('\n'), /Review the differences above, then commit/);
    assert.deepEqual(r.error, []);
  }
});

test('a failing or killed visual test fails the update with its own status', () => {
  const failed = updater({child: rewrite('{"v":2}', 3)});
  assert.equal(failed.status, 3);
  assert.match(failed.error.join('\n'), /NOT updated.*failed \(exit 3\)/);
  assert.deepEqual(failed.log, []);
  const killed = updater({child: () => ({status: null, signal: 'SIGKILL'})});
  assert.equal(killed.status, 1);
  assert.match(killed.error.join('\n'), /NOT updated.*did not finish.*SIGKILL/);
});


// ---- demo_driver cleanup: fake owned handles and a manually fired clock only ----
function cleanupFixture() {
  const events = [], diagnostics = [];
  let socketAck, chromeAck, serverAck, fireTimer;
  const wsClosed = new Promise(resolve => { socketAck = resolve; });
  const chromeClosed = new Promise(resolve => { chromeAck = resolve; });
  const resources = {profile: 'owned-profile', wsClosed, chromeClosed,
    ws: {close() { events.push('ws-request'); }},
    chrome: {exitCode: null, signalCode: null, kill() { events.push('chrome-request'); }},
    server: {close(callback) { events.push('server-request'); serverAck = callback; }}};
  const options = {removeProfile: async profile => { assert.equal(profile, 'owned-profile'); events.push('removed'); },
    setTimer(callback, milliseconds) { assert.equal(milliseconds, 5000); fireTimer = callback; return 17; },
    clearTimer(id) { assert.equal(id, 17); events.push('timer-cleared'); },
    diagnostic: (stage, pending) => diagnostics.push({stage, pending})};
  const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  return {resources, options, events, diagnostics, socketAck, chromeAck, tick,
    serverAck: error => serverAck(error), timeout: () => fireTimer()};
}

test('cleanup waits for socket, child stdio and server acknowledgements before removing its profile', async () => {
  const f = cleanupFixture();
  let finished = false;
  const closing = closeDemoResources(f.resources, f.options).then(() => { finished = true; });
  await f.tick();
  assert.deepEqual(f.events, ['ws-request', 'chrome-request', 'server-request']);
  f.socketAck(); f.serverAck();
  f.resources.chrome.exitCode = 0;  // An exit code is not a closed stdio acknowledgement.
  await f.tick();
  assert.equal(finished, false);
  assert.equal(f.events.includes('removed'), false);
  f.chromeAck(); await closing;
  assert.deepEqual(f.events.slice(-2), ['timer-cleared', 'removed']);
  assert.deepEqual(f.diagnostics, [{stage: 'closed', pending: []}]);
});

test('cleanup handles an already exited child without another termination request', async () => {
  const f = cleanupFixture();
  f.resources.chrome.exitCode = 0;
  f.socketAck(); f.chromeAck();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await closing;
  assert.equal(f.events.includes('chrome-request'), false);
  assert.equal(f.events.includes('removed'), true);
});

test('cleanup still attempts other resources and preserves the original close failure', async () => {
  const f = cleanupFixture(), failure = new Error('socket-close-failed');
  f.resources.ws.close = () => { throw failure; };
  f.chromeAck();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck();
  await assert.rejects(closing, error => error instanceof AggregateError && error.errors.includes(failure));
  assert.equal(f.events.includes('chrome-request'), true);
  assert.equal(f.events.includes('removed'), false);
  assert.equal(f.events.at(-1), 'timer-cleared');
});

test('missing close acknowledgement times out as failure without removal or a fake closed marker', async () => {
  const f = cleanupFixture();
  f.socketAck();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await f.tick(); f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message === 'demo cleanup did not close: chrome-stdio'));
  assert.equal(f.events.includes('removed'), false);
  assert.deepEqual(f.diagnostics, [{stage: 'timeout', pending: ['chrome-stdio']}]);
  assert.equal(f.events.at(-1), 'timer-cleared');
});

test('profile removal failure propagates after resource closure and clears the timer', async () => {
  const f = cleanupFixture(), failure = new Error('profile-remove-failed');
  f.socketAck(); f.chromeAck();
  f.options.removeProfile = async () => { throw failure; };
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck();
  await assert.rejects(closing, error => error === failure);
  assert.equal(f.events.at(-1), 'timer-cleared');
  assert.deepEqual(f.diagnostics, []);
});

test('partial startup still waits for its server and preserves a server-close error', async () => {
  const f = cleanupFixture(), failure = new Error('server-close-failed');
  delete f.resources.ws; delete f.resources.chrome;
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(failure);
  await assert.rejects(closing, error => error instanceof AggregateError && error.errors.includes(failure));
  assert.deepEqual(f.events, ['server-request', 'timer-cleared']);
});

test('a diagnostic failure cannot hide the original incomplete cleanup', async () => {
  const f = cleanupFixture();
  f.options.diagnostic = () => { throw new Error('diagnostic-failed'); };
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message.startsWith('demo cleanup did not close:')));
  assert.equal(f.events.includes('removed'), false);
  assert.equal(f.events.at(-1), 'timer-cleared');
});

// ---- demo_driver: an exception in a virtual timer callback is a page error ----
// The page-side clock runs here in a vm context. Its MessageChannel is a stub so no port keeps Node alive.
function pageClock() {
  const reported = [], logged = [];
  const later = fn => setImmediate(fn);
  class StubChannel {
    constructor() { this.port1 = {}; this.port2 = {postMessage: () => later(() => this.port1.onmessage && this.port1.onmessage())}; }
  }
  const win = {setTimeout, clearTimeout, requestAnimationFrame: cb => later(cb),
    reportError: error => reported.push(String(error && error.message || error))};
  const sandbox = {window: win, MessageChannel: StubChannel, localStorage: {clear() {}}, requestAnimationFrame: win.requestAnimationFrame,
    console: {error: error => logged.push(String(error && error.message || error))}};
  vm.createContext(sandbox);
  vm.runInContext(CLOCK_SOURCE, sandbox);
  return {win, clock: win.__vclock, reported, logged};
}

test('an exception thrown by a virtual setTimeout callback is reported as uncaught, and other timers still run', async () => {
  const {win, clock, reported, logged} = pageClock();
  const ran = [];
  win.setTimeout(() => ran.push('first'), 10);
  win.setTimeout(() => { throw new Error('boom in timeout'); }, 20);
  win.setTimeout(() => ran.push('after the throw'), 30);
  await clock.advance(100);
  assert.deepEqual(reported, ['boom in timeout']);
  assert.deepEqual(ran, ['first', 'after the throw']);
  assert.deepEqual(logged, [], 'not merely logged');
  assert.equal(clock.now(), 100);
  assert.equal(clock.pending(), 0);
});

test('every throw of a virtual setInterval callback is reported and the interval keeps running', async () => {
  const {win, clock, reported} = pageClock();
  let calls = 0;
  win.setInterval(() => { calls += 1; if (calls % 2 === 0) throw new Error(`boom ${calls}`); }, 10);
  await clock.advance(45);
  assert.equal(calls, 4);
  assert.deepEqual(reported, ['boom 2', 'boom 4']);
  assert.equal(clock.pending(), 1);
});

test('timers that do not throw report nothing', async () => {
  const {win, clock, reported, logged} = pageClock();
  win.setTimeout(() => {}, 5);
  win.setInterval(() => {}, 7);
  await clock.advance(50);
  assert.deepEqual([reported, logged], [[], []]);
});

test('without reportError the exception is still logged, never swallowed', async () => {
  const {win, clock, reported, logged} = pageClock();
  delete win.reportError;
  win.setTimeout(() => { throw new Error('old browser'); }, 5);
  await clock.advance(10);
  assert.deepEqual([reported, logged], [[], ['old browser']]);
});

test('the driver lists a timer callback exception in errors, so the visual test sees it',
  {skip: skipReason() || false, timeout: 150000}, async () => {
    const d = await openDemo({reducedMotion: true});
    try {
      await d.load();
      assert.deepEqual(d.errors, [], 'the demo itself loads without page errors');
      await d.evaluate(`window.__ran = [];
        setTimeout(() => { throw new Error('boom in timeout'); }, 10);
        setTimeout(() => window.__ran.push('after the throw'), 20);
        let n = 0;
        setInterval(() => { if (++n === 2) throw new Error('boom in interval'); }, 30);`);
      await d.advance(100);
      await d.frames();
      assert.equal(d.errors.length, 2, JSON.stringify(d.errors));
      assert.match(d.errors[0], /boom in timeout/);
      assert.match(d.errors[1], /boom in interval/);
      assert.deepEqual(await d.evaluate('window.__ran'), ['after the throw']);
      assert.throws(() => assert.deepEqual(d.errors, []), 'the assertion the visual test makes now fails');
    } finally {
      await d.close();
    }
  });
