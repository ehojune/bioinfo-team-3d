// Checks for the visual-test tooling itself (#546, remaining review notes of PR #599): the layout snapshot's color
// comparison, the baseline updater's exit status, the virtual page clock, and what the driver reports. No Chrome is
// needed except where a test says so.
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {DEFAULT_TOLERANCE, diffView, sameColor, colorChannels} = require('./support/layout_snapshot.cjs');
const {updateBaseline} = require('../scripts/update_visual_baseline.cjs');
const vm = require('node:vm');
const {openDemo, skipReason, CLOCK_SOURCE, closeDemoResources, demoLifecycleTrace, requestBrowserShutdown,
  CLEANUP_MS, GRACEFUL_CLEANUP_MS, JOB_STARTING_BROWSER, chromeStderrWatcher, profileEntryCount, BROWSER_CREATED,
  portReadCounter, workBudget, CLOSE_RESERVE_MS} = require('./support/demo_driver.cjs');

// Synchronous fake clock/observer only; no browser, timer or event loop.
test('demo lifecycle trace measures from its own origin and between observed boundaries', () => {
  const rows = [];
  let now = 400;
  const trace = demoLifecycleTrace(row => rows.push(row), () => now);
  trace('open-start');
  now = 425; trace('open-ready');
  now = 430; trace('navigate-start');
  now = 630; trace('navigate-ready');
  assert.deepEqual(rows, [
    {stage:'open-start', elapsed_ms:0, since_previous_ms:0},
    {stage:'open-ready', elapsed_ms:25, since_previous_ms:25},
    {stage:'navigate-start', elapsed_ms:30, since_previous_ms:5},
    {stage:'navigate-ready', elapsed_ms:230, since_previous_ms:200}]);
  const phone = [];
  demoLifecycleTrace(row => phone.push(row), () => now)('open-start');
  assert.deepEqual(phone, [{stage:'open-start', elapsed_ms:0, since_previous_ms:0}]);
  assert.equal(rows.some(row => row.stage === 'boot-ready'), false, 'no fabricated unobserved completion');
});

test('demo lifecycle trace is bounded and disabled observers do not sample the clock', () => {
  let sampled = 0;
  const rows = [], clock = () => ++sampled;
  const trace = demoLifecycleTrace(row => rows.push(row), clock);
  for (let i = 0; i < 40; i++) trace('boot-start');
  assert.equal(rows.length, 16);
  assert.equal(sampled, 16);
  let disabledSamples = 0;
  demoLifecycleTrace(undefined, () => ++disabledSamples)('open-start');
  assert.equal(disabledSamples, 0);
});

test('#833 server and waiting observations keep their own budget and never move or displace a boundary', () => {
  const rows = [];
  let now = 0;
  const trace = demoLifecycleTrace(row => rows.push(row), () => now);
  trace('open-start');
  now = 100; trace('target-ready', {json_attempts: 3, json_unanswered: 2, json_without_page: 0});
  now = 110; trace('navigate-start');
  now = 140; trace.observe('server-request');
  now = 150; trace.observe('server-response', {status: 200});
  now = 5110; trace.observe('navigate-waiting', {page_requested: true, page_responded: true});
  for (let i = 0; i < 10; i++) trace.observe('navigate-waiting');
  now = 5200; trace('navigate-ready');
  assert.deepEqual(rows.slice(1, 6), [
    {stage:'target-ready', elapsed_ms:100, since_previous_ms:100, json_attempts:3, json_unanswered:2, json_without_page:0},
    {stage:'navigate-start', elapsed_ms:110, since_previous_ms:10},
    {stage:'server-request', elapsed_ms:140, since_previous_ms:30},
    {stage:'server-response', elapsed_ms:150, since_previous_ms:40, status:200},
    {stage:'navigate-waiting', elapsed_ms:5110, since_previous_ms:5000, page_requested:true, page_responded:true}]);
  assert.equal(rows.filter(row => ['server-request', 'server-response', 'navigate-waiting'].includes(row.stage)).length, 6);
  assert.deepEqual(rows.at(-1), {stage:'navigate-ready', elapsed_ms:5200, since_previous_ms:5090});
  for (let i = 0; i < 20; i++) trace('boot-start');
  assert.equal(rows.filter(row => !['server-request', 'server-response', 'navigate-waiting'].includes(row.stage)).length, 16);
});

test('#833 the Job wrapper marker counts only as the first stderr line and leaves only counts', () => {
  const seen = [];
  const watch = chromeStderrWatcher(() => seen.push('marker'));
  watch.feed(JOB_STARTING_BROWSER.slice(0, 7));  // a marker split across chunks, Windows line ending
  watch.feed(JOB_STARTING_BROWSER.slice(7) + '\r\n[chrome] warn');
  watch.feed(` later ${JOB_STARTING_BROWSER}\n`);  // the same text inside Chrome's output is only output
  assert.deepEqual(seen, ['marker']);
  assert.equal(watch.marked(), true);
  assert.equal(watch.text(), `[chrome] warn later ${JOB_STARTING_BROWSER}\n`);
  assert.equal(watch.chromeBytes(), Buffer.byteLength(watch.text()));
  // The exact marker and CR in one chunk, LF in the next: still the marker line, and only once.
  const split = [];
  const crlf = chromeStderrWatcher(() => split.push('marker'));
  crlf.feed(JOB_STARTING_BROWSER + '\r');
  crlf.feed('\nchrome');
  assert.deepEqual(split, ['marker']);
  assert.equal(crlf.text(), 'chrome');
  // No marker first (Linux, or a wrapper that never reached Chrome): nothing is recorded and every byte is Chrome's.
  for (const first of ['DevTools listening\n', `x${JOB_STARTING_BROWSER}\n`, `${JOB_STARTING_BROWSER} extra\n`,
                       'LABHQ_MEMORY_LIMIT_SETUP: OSError: denied\n']) {
    const plain = chromeStderrWatcher(() => assert.fail('no marker'));
    plain.feed(first);
    assert.equal(plain.marked(), false);
    assert.equal(plain.text(), first);
    assert.equal(plain.chromeBytes(), Buffer.byteLength(first));
  }
  const failing = chromeStderrWatcher(() => { throw new Error('observer'); });
  failing.feed(`${JOB_STARTING_BROWSER}\n`);
  assert.equal(failing.marked(), true);
  assert.equal(failing.chromeBytes(), 0);
  assert.equal(profileEntryCount(path.join(os.tmpdir(), 'labhq-no-such-profile-833')), null);
});

test('#833 the start wait records the marker and one counts-only waiting row; acceptance stays DevToolsActivePort', () => {
  const source = fs.readFileSync(path.join(__dirname, 'support/demo_driver.cjs'), 'utf8');
  const start = source.slice(source.indexOf("trace('chrome-spawned');"), source.indexOf("trace('debug-port-ready');"));
  // The only value that ends the wait is still the port read from Chrome's own DevToolsActivePort file.
  assert.equal((start.match(/debug = /g) || []).length, 1);
  assert.match(start, /debug = fs\.readFileSync\(path\.join\(profile, 'DevToolsActivePort'\), 'utf8'\)/);
  assert.match(start, /while \(!debug && !exited && Date\.now\(\) < startDeadline\)/);
  // #833: the 60 s start budget, ended no later than an opt-in work deadline (budget.cap is the identity without one).
  assert.match(source, /const startDeadline = budget\.cap\(Date\.now\(\) \+ START_MS\);/);
  assert.match(source, /const START_MS = 60000;/);
  assert.equal((start.match(/trace\.observe\('chrome-waiting'/g) || []).length, 1);
  assert.match(start, /job_marker_seen: stderrWatch\.marked\(\), chrome_created_seen: stderrWatch\.created\(\),\s*port_reads: portReads\.counts\(\), profile_entries: profileEntryCount\(profile\),\s*chrome_stderr_bytes: stderrWatch\.chromeBytes\(\)\}/);
  assert.match(start, /catch \(error\) \{ portReads\.failed\(error\); \}/);
  assert.match(source, /chromeStderrWatcher\(\(\) => trace\.observe\('job-ready'\), \(\) => trace\.observe\('chrome-created'\)\)/);
});

test('#833 the created line counts only right after the Job line, and a read failure leaves only a fixed code', () => {
  const seen = [];
  const watch = chromeStderrWatcher(() => seen.push('job'), () => seen.push('created'));
  watch.feed(`${JOB_STARTING_BROWSER}\r\n${BROWSER_CREATED.slice(0, 9)}`);  // split anywhere, CR LF
  assert.equal(watch.created(), false);
  watch.feed(`${BROWSER_CREATED.slice(9)}\r\n[chrome] warn ${BROWSER_CREATED}\n`);
  assert.deepEqual(seen, ['job', 'created']);
  assert.equal(watch.marked() && watch.created(), true);
  assert.equal(watch.text(), `[chrome] warn ${BROWSER_CREATED}\n`);  // later, the same text is only Chrome's output
  assert.equal(watch.chromeBytes(), Buffer.byteLength(watch.text()));
  // Chrome's own output first: the Job line still counts, the created line is not seen and the output is Chrome's.
  const early = chromeStderrWatcher(() => {}, () => assert.fail('not after Chrome output'));
  early.feed(`${JOB_STARTING_BROWSER}\nDevTools listening\n${BROWSER_CREATED}\n`);
  assert.equal(early.marked(), true);
  assert.equal(early.created(), false);
  assert.equal(early.text(), `DevTools listening\n${BROWSER_CREATED}\n`);
  // Without the Job line first, the created line is only output too.
  const plain = chromeStderrWatcher(() => assert.fail('no Job line'), () => assert.fail('no Job line'));
  plain.feed(`${BROWSER_CREATED}\n`);
  assert.equal(plain.created(), false);
  assert.equal(plain.chromeBytes(), Buffer.byteLength(`${BROWSER_CREATED}\n`));

  const reads = portReadCounter();
  for (const code of ['ENOENT', 'ENOENT', 'EBUSY', 'EACCES', 'EPERM', 'EISDIR', undefined]) {
    reads.failed(Object.assign(new Error('C:/private/profile/DevToolsActivePort secret'), {code}));
  }
  reads.empty();
  reads.failed(null);
  const counts = reads.counts();
  assert.deepEqual(counts, {ENOENT: 2, EBUSY: 1, EACCES: 1, EPERM: 1, other: 3, empty: 1});
  counts.ENOENT = 99;
  assert.equal(reads.counts().ENOENT, 2, 'a snapshot, not the live counter');
  assert.doesNotMatch(JSON.stringify(reads.counts()), /private|secret|DevToolsActivePort/);
});

// ---- #833: one opt-in work deadline (fake clock and timers only; nothing waits in real time) ----
function fakeTimers(start = 0) {
  const clock = {now: start}, timers = new Map();
  let seq = 0;
  return {clock, timers, now: () => clock.now,
    setTimer(fn, ms) { const id = ++seq; timers.set(id, {fn, ms, at: clock.now + ms}); return id; },
    clearTimer(id) { timers.delete(id); },
    fire(id) { const t = timers.get(id); timers.delete(id); clock.now = Math.max(clock.now, t.at); t.fn(); }};
}

test('#833 without a work deadline every wait runs exactly as before: no timer, no signal, no expiry', async () => {
  const t = fakeTimers();
  const budget = workBudget(undefined, {now: t.now, setTimer: t.setTimer, clearTimer: t.clearTimer,
    onExpired: () => assert.fail('no deadline')});
  let signal = 'unset';
  const result = budget.bound('Runtime.evaluate', given => { signal = given; return Promise.resolve(7); });
  assert.equal(await result, 7);
  assert.equal(signal, undefined);
  assert.equal(t.timers.size, 0);
  assert.equal(budget.remaining(), Infinity);
  assert.equal(budget.cap(61000), 61000);
  budget.check('anything');
  assert.equal(budget.limited, false);
});

test('#833 one shared work deadline aborts a pending wait, drops it once and stops all later work', async () => {
  const t = fakeTimers(1000), expired = [];
  const budget = workBudget(1500, {now: t.now, setTimer: t.setTimer, clearTimer: t.clearTimer,
    onExpired: stage => expired.push(stage)});
  assert.equal(budget.cap(61000), 1500);
  assert.equal(budget.cap(1200), 1200);
  // A wait that settles in time clears its own timer; nothing is left behind.
  assert.equal(await budget.bound('Page.enable', () => Promise.resolve('ok')), 'ok');
  assert.equal(t.timers.size, 0);
  // A wait still pending at the deadline: its signal aborts, the caller's drop runs once, the result is a failure.
  let aborted = 0, dropped = 0, settleLate;
  const pending = budget.bound('devtools-targets', signal => new Promise(resolve => {
    settleLate = resolve;
    signal.addEventListener('abort', () => aborted++);
  }), () => dropped++);
  await Promise.resolve();  // the wait has started
  assert.equal(t.timers.size, 1);
  const [[id, timer]] = t.timers;
  assert.equal(timer.ms, 500, 'the timer is the remaining shared budget');
  t.clock.now = 1200;  // the same budget is shared: less remains later for every caller
  assert.equal(budget.remaining(), 300);
  t.fire(id);
  await assert.rejects(pending, error => error.code === 'DEMO_WORK_DEADLINE' && error.stage === 'devtools-targets');
  assert.equal(aborted, 1);
  assert.equal(dropped, 1);
  settleLate('late answer');  // a late result changes nothing
  await Promise.resolve();
  assert.equal(dropped, 1);
  // The next viewport or CDP call starts nothing once the budget is spent, and the final phase was recorded once.
  t.clock.now = 1500;
  let started = 0;
  await assert.rejects(budget.bound('Page.navigate', () => { started++; return Promise.resolve(); }),
    error => error.stage === 'Page.navigate');
  assert.throws(() => budget.check('open-start'), error => error.code === 'DEMO_WORK_DEADLINE' && error.stage === 'open-start');
  assert.equal(started, 0);
  assert.equal(t.timers.size, 0);
  assert.deepEqual(expired, ['devtools-targets']);
});

test('#833 startup and UI budgets name the failed phase without losing abort or the outer cap', async () => {
  const t = fakeTimers(1000), expired = [];
  const outerWorkDeadline = 52000;  // a 60 s watchdog with 8 s reserved for owned cleanup
  const startup = workBudget(Math.min(outerWorkDeadline, t.now() + 30000), {
    phase: 'startup', now: t.now, setTimer: t.setTimer, clearTimer: t.clearTimer,
    onExpired: stage => expired.push(['startup', stage])});
  let aborted = 0;
  const waiting = startup.bound('chrome-start', signal => new Promise(() => {
    signal.addEventListener('abort', () => aborted++);
  }));
  await Promise.resolve();
  const [[id, timer]] = t.timers;
  assert.equal(timer.ms, 30000);
  t.fire(id);
  await assert.rejects(waiting, error => error.code === 'DEMO_WORK_DEADLINE'
    && error.phase === 'startup' && error.stage === 'chrome-start'
    && /demo startup deadline reached/.test(error.message));
  assert.equal(aborted, 1);
  assert.equal(t.timers.size, 0);
  const ui = workBudget(Math.min(outerWorkDeadline, t.now() + 20000), {
    phase: 'ui', now: t.now, setTimer: t.setTimer, clearTimer: t.clearTimer,
    onExpired: stage => expired.push(['ui', stage])});
  t.clock.now = 52000;
  assert.throws(() => ui.check('Page.navigate'), error => error.phase === 'ui'
    && error.stage === 'Page.navigate' && /demo ui deadline reached/.test(error.message));
  assert.deepEqual(expired, [['startup', 'chrome-start'], ['ui', 'Page.navigate']]);
});

test('#833 a wait that fails before the deadline keeps its own error and clears its timer', async () => {
  const t = fakeTimers(0);
  const budget = workBudget(10000, {now: t.now, setTimer: t.setTimer, clearTimer: t.clearTimer,
    onExpired: () => assert.fail('not expired')});
  const failure = new Error('CDP socket failed: refused');
  await assert.rejects(budget.bound('cdp-socket', () => Promise.reject(failure)), error => error === failure);
  await assert.rejects(budget.bound('cdp-socket', () => { throw failure; }), error => error === failure);
  assert.equal(t.timers.size, 0);
});

test('demo lifecycle observer and clock failures cannot replace an operation failure', () => {
  const original = new Error('original operation failure');
  for (const trace of [demoLifecycleTrace(() => { throw new Error('observer'); }, () => 1),
                       demoLifecycleTrace(() => {}, () => { throw new Error('clock'); })]) {
    assert.throws(() => { trace('navigate-start'); throw original; }, error => error === original);
  }
});

test('one startup and load fit the trace bound while an incomplete startup invents no ready stage', () => {
  const rows = [];
  let now = 0;
  const trace = demoLifecycleTrace(row => rows.push(row), () => now++);
  const stages = ['open-start','profile-ready','server-ready','chrome-spawned','debug-port-ready',
    'target-ready','socket-ready','protocol-ready','open-ready','navigate-start','navigate-ready',
    'boot-start','boot-ready','frames-start','frames-ready'];
  stages.forEach(trace);
  assert.deepEqual(rows.map(row => row.stage), stages);
  assert.equal(rows.at(-1).elapsed_ms, 14);
  const incomplete = [];
  const partial = demoLifecycleTrace(row => incomplete.push(row), () => now++);
  stages.slice(0, 4).forEach(partial);
  assert.deepEqual(incomplete.map(row => row.stage), stages.slice(0, 4));
  assert.equal(incomplete.some(row => row.stage === 'debug-port-ready'), false);
});

// Evaluate the actual driver in an isolated VM with only owned synthetic
// resources. These async contracts run in CI; they launch no browser/service.
function startupFixture({port = 55921, ports = null, listenError = null, closeError = null, platform, env = {}, existing = [],
  wrapperPresent = false, spawned = null, globals = {}} = {}) {
  const events = [], profile = path.join('owned-root', 'owned-profile');
  let onError, listens = 0;
  const drawn = ports || [port];  // #1072: the port each successive listen(0) is given; the last one repeats
  const server = {
    once(name, fn) { assert.equal(name, 'error'); onError = fn; },
    removeListener(name, fn) { assert.equal(name, 'error'); assert.equal(fn, onError); onError = null; },
    listen(port, host, ready) {
      assert.equal(port, 0); assert.equal(host, '127.0.0.1');
      events.push('listen');
      listens += 1;
      if (listenError) { const fail = onError; onError = null; fail(listenError); }
      else ready();
    },
    address: () => ({port: drawn[Math.min(listens, drawn.length) - 1]}),
    close(done) { events.push('server-close'); done(closeError); }
  };
  const fakeFs = {
    existsSync: name => name === 'owned-chrome' || existing.includes(name)
      || (wrapperPresent && name.endsWith(path.join('support', 'windows_job_browser.py'))),
    mkdirSync() {},
    mkdtempSync() { events.push('profile-create'); return profile; },
    rmSync(dir) { assert.equal(dir, profile); events.push('profile-remove'); }
  };
  const modules = {'node:fs':fakeFs, 'node:path':path, 'node:os':{tmpdir:() => 'owned-root'},
    'node:http':{createServer:() => server},
    'node:child_process':{spawn(command, args, options) {
      if (!spawned) throw new Error('unexpected browser spawn');
      spawned.push({command, args, options});
      throw new Error('stop after spawn');  // only the launch command is under test; no browser runs
    }},
    './test_font.cjs':{}, './mini_ws.cjs':{}};
  const sandbox = {module:{exports:{}}, __dirname:path.join(__dirname, 'support'),  // the driver's own folder
    require(name) { assert.ok(Object.hasOwn(modules, name), name); return modules[name]; },
    process:{env:{LABHQ_TEST_CHROME:'owned-chrome', ...env}, platform, getActiveResourcesInfo:() => []},
    console:{error() {}},
    setTimeout(fn, ms) { assert.equal(ms, 5000); events.push('timer-set'); return 7; },
    clearTimeout(id) { assert.equal(id, 7); events.push('timer-clear'); }, ...globals};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'support/demo_driver.cjs'), 'utf8'), sandbox);
  return {open:sandbox.module.exports.openDemo, exports:sandbox.module.exports, events,
    hasListenErrorHandler:() => Boolean(onError)};
}

// #1072: a reserved draw is closed and drawn again; only this many in a row are refused.
const RESERVED_DRAWS = 8;
const reservedDraws = (extra = []) => ['profile-create', ...Array(RESERVED_DRAWS - 1).fill(['listen', 'server-close']).flat(), 'listen',
  ...extra];

test('reserved-port startup failure closes its acquired server before removing the profile', async () => {
  const f = startupFixture();  // every draw is 55921
  await assert.rejects(f.open(), /static server landed on a reserved port 55921/);
  assert.deepEqual(f.events, reservedDraws(['timer-set','server-close','timer-clear','profile-remove']));
  assert.equal(f.hasListenErrorHandler(), false);
});

test('a reserved port drawn by the OS is closed and drawn again instead of failing the startup', async () => {
  const spawned = [];
  const f = startupFixture({ports:[55921, 8787, 40000], platform:'win32', env:{LABHQ_TEST_PYTHON:PYTHON}, existing:[PYTHON],
    wrapperPresent:true, spawned});
  await assert.rejects(f.open(), /stop after spawn/);  // startup went on to launch the browser on the third draw
  assert.equal(spawned.length, 1);
  assert.deepEqual(f.events.slice(0, 7), ['profile-create','listen','server-close','listen','server-close','listen','timer-set']);
  assert.equal(f.events.filter(event => event === 'listen').length, 3);
  assert.equal(f.events.filter(event => event === 'server-close').length, 3);  // two redraws, then the cleanup of the owned one
  assert.deepEqual(f.events.slice(-2), ['timer-clear','profile-remove']);
});

test('listen failure rejects startup and removes the profile without pretending a server was acquired', async () => {
  const failure = new Error('synthetic bind failure');
  const f = startupFixture({listenError:failure});
  await assert.rejects(f.open(), error => error === failure);
  assert.deepEqual(f.events, ['profile-create','listen','timer-set','timer-clear','profile-remove']);
  assert.equal(f.hasListenErrorHandler(), false);
});

test('startup cleanup failure retains both errors and does not remove an unclosed profile', async () => {
  const failure = new Error('synthetic server close failure');
  const f = startupFixture({closeError:failure});
  await assert.rejects(f.open(), error => error.message === 'demo startup and cleanup failed' &&
    error.errors[0].message.includes('reserved port 55921') && error.errors[1].errors.includes(failure));
  // The redraw's close already failed, so the server is handed over unchanged and its own close fails again.
  assert.equal(f.events.filter(event => event === 'server-close').length, 2);
  assert.equal(f.events.filter(event => event === 'listen').length, 1);
  assert.equal(f.events.includes('profile-remove'), false);
});

// ---- #842/#843: Windows Chrome under the product's owned KILL_ON_CLOSE Job wrapper (launch command only) ----
const PYTHON = 'C:/hosted/python.exe';  // forward slashes: still an absolute Windows path

test('Windows starts the owned Chrome under the Job wrapper with the explicit interpreter', async () => {
  const spawned = [];
  const f = startupFixture({port:40000, platform:'win32', env:{LABHQ_TEST_PYTHON:PYTHON}, existing:[PYTHON],
    wrapperPresent:true, spawned});
  await assert.rejects(f.open(), /stop after spawn/);
  assert.equal(spawned.length, 1);
  const {command, args, options} = spawned[0];
  assert.equal(command, PYTHON);
  // Arrays built inside the vm context are copied into this realm before a strict deep comparison.
  assert.deepEqual(Array.from(args.slice(0, 6)), ['-I', f.exports.WINDOWS_JOB_WRAPPER, '--windows-job',
    String(f.exports.WINDOWS_JOB_LIMIT_BYTES), '--', 'owned-chrome']);
  assert.ok(f.exports.WINDOWS_JOB_WRAPPER.endsWith(path.join('support', 'windows_job_browser.py')));
  assert.equal(args[6], '--headless=new');
  assert.equal(args.at(-1), 'about:blank');
  assert.deepEqual(Array.from(options.stdio), ['ignore', 'ignore', 'pipe'], 'the owned stdio and its close proof stay');
  assert.equal(options.windowsHide, true);
  // The spawn failed before a child existed: the acquired server and profile are still cleaned up.
  assert.deepEqual(f.events, ['profile-create','listen','timer-set','server-close','timer-clear','profile-remove']);
});

for (const [label, env, existing, wrapperPresent] of [
  ['no interpreter', {}, [], true],
  ['a relative interpreter', {LABHQ_TEST_PYTHON:'python'}, ['python'], true],
  ['a Store alias', {LABHQ_TEST_PYTHON:'C:/Users/x/AppData/Local/Microsoft/WindowsApps/python.exe'},
    ['C:/Users/x/AppData/Local/Microsoft/WindowsApps/python.exe'], true],
  ['a missing interpreter', {LABHQ_TEST_PYTHON:PYTHON}, [], true],
  ['a missing wrapper', {LABHQ_TEST_PYTHON:PYTHON}, [PYTHON], false],
]) {
  test(`Windows refuses to start Chrome with ${label}, before any profile, server or browser exists`, async () => {
    const spawned = [];
    const f = startupFixture({port:40000, platform:'win32', env, existing, wrapperPresent, spawned});
    await assert.rejects(f.open(), /LABHQ_TEST_PYTHON must name|owned Windows Job wrapper is missing/);
    assert.deepEqual(f.events, []);
    assert.deepEqual(spawned, []);
  });
}

test('Linux keeps spawning Chrome directly, whatever LABHQ_TEST_PYTHON says', async () => {
  const spawned = [];
  const f = startupFixture({port:40000, platform:'linux', env:{LABHQ_TEST_PYTHON:PYTHON}, spawned});
  await assert.rejects(f.open(), /stop after spawn/);
  assert.equal(spawned[0].command, 'owned-chrome');
  assert.equal(spawned[0].args[0], '--headless=new');
  assert.equal(spawned[0].args.includes('--windows-job'), false);
  const [direct, directArgs] = f.exports.browserCommand('owned-chrome', ['a'], null);
  assert.equal(direct, 'owned-chrome');
  assert.deepEqual(Array.from(directArgs), ['a']);
});

// ---- #833: the opt-in work deadline inside the actual driver (vm, fake resources, fake clock) ----
test('#833 the reserve after the work deadline covers one close: its deadline, profile removal and process exit', () => {
  assert.equal(CLOSE_RESERVE_MS, CLEANUP_MS + 20 * 100 + 1000);
  assert.ok(CLOSE_RESERVE_MS < 60000 / 2, 'most of the 60 s watchdog is still work');
});

test('#833 a work deadline reached once the server is owned closes it once and opens nothing more', async () => {
  const times = [1000, 3000];  // open-start check, then server-ready check
  const rows = [];
  const f = startupFixture({port: 40000, globals: {Date: {now: () => times.length > 1 ? times.shift() : times[0]},
    performance: {now: () => 0}}});
  const opts = {workDeadline: 2000, diagnostic: row => rows.push(JSON.parse(JSON.stringify(row)))};
  await assert.rejects(f.open(opts), error => error.code === 'DEMO_WORK_DEADLINE' && error.stage === 'server-ready');
  assert.deepEqual(f.events, ['profile-create','listen','timer-set','server-close','timer-clear','profile-remove']);
  assert.deepEqual(rows.filter(row => row.stage === 'work-deadline').map(row => row.during), ['server-ready']);
  // The next viewport shares the spent budget: it fails before any profile, server or browser.
  await assert.rejects(f.open(opts), error => error.code === 'DEMO_WORK_DEADLINE' && error.stage === 'open-start');
  assert.deepEqual(f.events, ['profile-create','listen','timer-set','server-close','timer-clear','profile-remove']);
});

test('#833 phased startup expiry closes the owned server before the outer watchdog', async () => {
  const times = [1000, 1000, 31001], rows = [];
  const f = startupFixture({port: 40000, globals: {Date: {now: () => times.length > 1 ? times.shift() : times[0]},
    performance: {now: () => 0}}});
  await assert.rejects(f.open({workDeadline: 52000, startupBudgetMs: 30000, uiBudgetMs: 20000,
    diagnostic: row => rows.push(JSON.parse(JSON.stringify(row)))}),
  error => error.code === 'DEMO_WORK_DEADLINE' && error.phase === 'startup' && error.stage === 'server-ready');
  assert.deepEqual(f.events, ['profile-create','listen','timer-set','server-close','timer-clear','profile-remove']);
  assert.deepEqual(rows.filter(row => row.stage === 'startup-deadline').map(row => row.during), ['server-ready']);
});

test('#833 phased run rejects missing bounds before it owns resources', async () => {
  const f = startupFixture({port: 40000});
  await assert.rejects(f.open({workDeadline: 52000, startupBudgetMs: 30000}),
    /phased demo needs an outer work deadline and positive startup\/UI budgets/);
  assert.deepEqual(f.events, []);
});

test('#833 a Chrome start still waiting at the work deadline stops there, records it and is cleaned up once', async () => {
  const t = fakeTimers(1000), events = [], rows = [];
  const handlers = {};
  const chrome = {exitCode: null, signalCode: null, stderr: {on() {}, closed: false},
    once(name, fn) { (handlers[name] ||= []).push(fn); }, on(name, fn) { (handlers[name] ||= []).push(fn); },
    kill() {
      events.push('chrome-kill');
      chrome.exitCode = 1;
      for (const name of ['exit', 'close']) for (const fn of (handlers[name] || []).splice(0)) fn(1, null);
    }};
  let onError;
  const server = {once(name, fn) { onError = fn; }, removeListener() { onError = null; },
    listen(port, host, ready) { events.push('listen'); ready(); }, address: () => ({port: 40000}),
    close(done) { events.push('server-close'); done(); }};
  const profile = path.join('owned-root', 'owned-profile');
  const fakeFs = {existsSync: name => name === 'owned-chrome', mkdirSync() {},
    mkdtempSync() { events.push('profile-create'); return profile; },
    readFileSync(name) {
      assert.equal(name, path.join(profile, 'DevToolsActivePort'));
      events.push('port-read');
      throw Object.assign(new Error('C:/private/profile secret'), {code: 'ENOENT'});
    },
    readdirSync: () => [], rmSync(dir) { assert.equal(dir, profile); events.push('profile-remove'); }};
  const modules = {'node:fs': fakeFs, 'node:path': path, 'node:os': {tmpdir: () => 'owned-root'},
    'node:http': {createServer: () => server},
    'node:child_process': {spawn() { events.push('spawn'); return chrome; }},
    './test_font.cjs': {}, './mini_ws.cjs': {}};
  const sandbox = {module: {exports: {}}, __dirname: path.join(__dirname, 'support'),
    require(name) { assert.ok(Object.hasOwn(modules, name), name); return modules[name]; },
    process: {env: {LABHQ_TEST_CHROME: 'owned-chrome'}, platform: 'linux', getActiveResourcesInfo: () => []},
    console: {error() {}}, Date: {now: t.now}, performance: {now: t.now}, AbortController,
    fetch() { throw new Error('no DevTools request before a published port'); },
    setTimeout: t.setTimer, clearTimeout: t.clearTimer};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'support/demo_driver.cjs'), 'utf8'), sandbox);
  const open = opts => sandbox.module.exports.openDemo(opts);
  // Fire the next fake timer whenever the driver has nothing else to do, until the open settles.
  const pump = async promise => {
    let settled = false;
    promise.then(() => { settled = true; }, () => { settled = true; });
    for (let i = 0; i < 100 && !settled; i++) {
      await new Promise(resolve => setImmediate(resolve));
      if (settled) break;
      const next = [...t.timers].sort(([a, x], [b, y]) => x.at - y.at || a - b)[0];
      if (next) t.fire(next[0]);
    }
    return promise;
  };
  const opts = {workDeadline: 1350, diagnostic: row => rows.push(JSON.parse(JSON.stringify(row)))};
  await assert.rejects(pump(open(opts)), error => error.code === 'DEMO_WORK_DEADLINE' && error.stage === 'chrome-start');
  assert.equal(t.clock.now, 1350, 'the start wait ends at the work deadline, not at its own 60 s budget');
  assert.deepEqual(events.filter(event => event !== 'port-read'),
    ['profile-create', 'listen', 'spawn', 'chrome-kill', 'server-close', 'profile-remove']);
  assert.equal(events.filter(event => event === 'port-read').length, 4);
  assert.equal(t.timers.size, 0, 'no wait or cleanup timer outlives the close');
  const final = rows.filter(row => row.stage === 'work-deadline');
  assert.deepEqual(final.map(row => [row.during, row.port_reads]), [['chrome-start', {ENOENT: 4}]]);
  assert.equal(rows.some(row => row.stage === 'debug-port-ready'), false);
  assert.doesNotMatch(JSON.stringify(rows), /private|secret|owned-profile/);
  // The same deadline then refuses the phone viewport before it creates anything.
  await assert.rejects(pump(open(opts)), error => error.stage === 'open-start');
  assert.equal(events.filter(event => event === 'profile-create').length, 1);
  assert.equal(events.filter(event => event === 'chrome-kill').length, 1);
});

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
test('browser shutdown requests graceful close before abrupt owned-child termination', () => {
  const events = [], failures = [];
  const chrome = {exitCode:null, signalCode:null, kill() { events.push('kill'); }};
  assert.equal(requestBrowserShutdown(chrome, () => { events.push('Browser.close'); return true; }, failures), true);
  assert.deepEqual(events, ['Browser.close']);
  assert.deepEqual(failures, []);
  chrome.exitCode = 0;  // Shared stdio and an owned browser can outlive the launcher.
  assert.equal(requestBrowserShutdown(chrome, () => { events.push('Browser.close'); return true; }, failures), true);
  assert.deepEqual(events, ['Browser.close', 'Browser.close']);
});

test('unavailable browser protocol retains owned-child fallback without killing an exited child', () => {
  for (const request of [undefined, () => false]) {
    let killed = 0;
    const chrome = {exitCode:null, signalCode:null, kill() { killed++; }};
    assert.equal(requestBrowserShutdown(chrome, request, []), false);
    assert.equal(killed, 1);
    chrome.exitCode = 0;
    assert.equal(requestBrowserShutdown(chrome, request, []), false);
    assert.equal(killed, 1);
  }
});

test('shutdown send and owned-child fallback errors are retained', () => {
  const sendError = new Error('send failed'), killError = new Error('kill failed');
  const failures = [];
  const chrome = {exitCode:null, signalCode:null, kill() { throw killError; }};
  assert.throws(() => requestBrowserShutdown(chrome, () => { throw sendError; }, failures), error => error === killError);
  assert.deepEqual(failures, [sendError]);
});

function cleanupFixture() {
  const events = [], diagnostics = [];
  let socketAck, chromeAck, serverAck, fireTimer, firePhase;
  const phaseCleared = [];
  const wsClosed = new Promise(resolve => { socketAck = resolve; });
  const chromeClosed = new Promise(resolve => { chromeAck = resolve; });
  const resources = {profile: 'owned-profile', wsClosed, chromeClosed,
    ws: {close() { events.push('ws-request'); }},
    chrome: {exitCode: null, signalCode: null, kill() { events.push('chrome-request'); }},
    server: {close(callback) { events.push('server-request'); serverAck = callback; }}};
  const options = {removeProfile: async profile => { assert.equal(profile, 'owned-profile'); events.push('removed'); },
    // One 5 s deadline (id 17) and the graceful phase inside it (id 16); no other timer.
    setTimer(callback, milliseconds) {
      if (milliseconds === GRACEFUL_CLEANUP_MS) { firePhase = callback; return 16; }
      assert.equal(milliseconds, CLEANUP_MS); fireTimer = callback; return 17;
    },
    clearTimer(id) {
      if (id === 16) { phaseCleared.push(id); return; }
      assert.equal(id, 17); events.push('timer-cleared');
    },
    diagnostic: (stage, pending) => diagnostics.push({stage, pending})};
  const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  return {resources, options, events, diagnostics, socketAck, chromeAck, tick, phaseCleared,
    serverAck: error => serverAck(error), timeout: () => fireTimer(), phase: () => firePhase(),
    phaseSet: () => firePhase !== undefined};
}

test('cleanup waits for socket, child stdio and server acknowledgements before removing its profile', async () => {
  const f = cleanupFixture();
  let finished = false;
  const closing = closeDemoResources(f.resources, f.options).then(() => { finished = true; });
  await f.tick();
  assert.deepEqual(f.events, ['chrome-request', 'ws-request', 'server-request']);
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
  assert.equal(f.phaseSet(), false, 'no owned Chrome was acquired, so there is no forced phase to wait for');
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

test('graceful cleanup keeps CDP open until actual child stdio close and still waits for socket acknowledgement', async () => {
  const f = cleanupFixture();
  f.resources.requestBrowserClose = () => { f.events.push('Browser.close'); return true; };
  let completed = false;
  const closing = closeDemoResources(f.resources, f.options).then(() => { completed = true; });
  await f.tick();
  assert.deepEqual(f.events, ['Browser.close', 'server-request']);
  f.serverAck(); f.resources.chrome.exitCode = 0;
  await f.tick();
  assert.equal(completed, false);
  assert.equal(f.events.includes('removed'), false);
  assert.equal(f.events.includes('ws-request'), false, 'exit is not stdio close');
  f.chromeAck(); await f.tick();
  assert.equal(f.events.includes('ws-request'), true);
  assert.equal(completed, false, 'socket acknowledgement still required');
  f.socketAck(); await closing;
  assert.equal(f.events.includes('chrome-request'), false);
  assert.deepEqual(f.events.slice(-2), ['timer-cleared', 'removed']);
});

test('stalled graceful cleanup keeps the five-second failure and requests owned-child fallback once', async () => {
  const f = cleanupFixture();
  f.resources.requestBrowserClose = () => { f.events.push('Browser.close'); return true; };
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await f.tick(); f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message.startsWith('demo cleanup did not close:')));
  assert.equal(f.events.filter(x => x === 'chrome-request').length, 1);
  assert.equal(f.events.filter(x => x === 'ws-request').length, 1);
  assert.equal(f.events.includes('removed'), false);
  assert.equal(f.diagnostics.some(row => row.stage === 'closed'), false);
  f.chromeAck(); f.socketAck(); await f.tick();
  assert.equal(f.events.filter(x => x === 'ws-request').length, 1);
  assert.equal(f.events.includes('removed'), false, 'late acknowledgements cannot reverse timeout failure');
});

function lateFixture() {
  const {EventEmitter} = require('node:events');
  const f = cleanupFixture(), rows = [], clock = {now: 100};
  const chrome = Object.assign(new EventEmitter(), {exitCode: null, signalCode: null,
    kill() { f.events.push('chrome-request'); }});
  f.resources.chrome = chrome;
  f.resources.requestBrowserClose = () => { f.events.push('Browser.close'); return true; };
  f.options.clock = () => clock.now;
  f.options.diagnostic = (stage, pending, extra) => rows.push({stage, pending, ...(extra || {})});
  return {...f, chrome, rows, clock};
}

test('after the deadline, late owned exit and closes are recorded with their delay but stay a failure (#842)', async () => {
  const f = lateFixture();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await f.tick(); f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message === 'demo cleanup did not close: chrome-stdio, websocket'));
  // The deadline's fallback still terminates the owned child once; observation was attached before it.
  assert.equal(f.events.filter(x => x === 'chrome-request').length, 1);
  // Without a fired graceful phase the deadline keeps its fallback, recorded as forced after the timeout row.
  assert.deepEqual(f.rows, [{stage: 'timeout', pending: ['chrome-stdio', 'websocket']},
                            {stage: 'forced', pending: ['chrome-stdio', 'websocket']}]);
  f.clock.now = 130; f.chrome.exitCode = 1; f.chrome.emit('exit', 1, null);
  f.clock.now = 180; f.chromeAck(); await f.tick();
  f.clock.now = 200; f.socketAck(); await f.tick();
  assert.deepEqual(f.rows.slice(2).map(row => [row.stage, row.after_deadline_ms]),
    [['late-chrome-exit', 30], ['late-chrome-stdio-close', 80], ['late-socket-close', 100]]);
  assert.equal(f.rows.some(row => row.stage === 'closed'), false);
  assert.equal(f.events.includes('removed'), false, 'a late acknowledgement never removes the profile');
});

test('an exit that is never observed leaves only the timeout record, and earlier acknowledgements are not late', async () => {
  const f = lateFixture();
  f.socketAck();  // the CDP socket closed before the deadline; only the owned child is still open
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await f.tick(); f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message.startsWith('demo cleanup did not close:')));
  await f.tick();
  assert.deepEqual(f.rows.map(row => row.stage), ['timeout', 'forced']);
  assert.equal(f.chrome.listenerCount('exit'), 1, 'only the observation waits; nothing claims the child closed');
  assert.equal(f.events.includes('removed'), false);
});

// ---- #842 cleanup budget: graceful part, then the owned child's forced close inside the same 5 s ----
const kills = f => f.events.filter(x => x === 'chrome-request').length;

test('a stalled graceful close is forced inside the deadline and completes only on the actual close (#842)', async () => {
  assert.ok(GRACEFUL_CLEANUP_MS < CLEANUP_MS && CLEANUP_MS === 5000);
  const f = lateFixture();
  let completed = false;
  const closing = closeDemoResources(f.resources, f.options).then(() => { completed = true; });
  await f.tick(); f.serverAck(); await f.tick();
  assert.equal(kills(f), 0, 'graceful first');
  f.phase();  // the graceful part of the budget ran out; the 5 s deadline has not
  assert.equal(kills(f), 1);
  assert.equal(f.events.filter(x => x === 'ws-request').length, 1);
  assert.deepEqual(f.rows.map(row => row.stage), ['forced']);
  f.chrome.exitCode = 1; f.chrome.emit('exit', 1, null); await f.tick();
  assert.equal(completed, false, 'exit alone is not the stdio close');
  f.chromeAck(); await f.tick();
  assert.equal(completed, false, 'the socket acknowledgement is still required');
  f.socketAck(); await closing;
  assert.equal(kills(f), 1, 'one forced termination, never repeated');
  assert.equal(f.events.filter(x => x === 'removed').length, 1);
  assert.deepEqual(f.rows.map(row => row.stage), ['forced', 'closed']);
  assert.deepEqual(f.phaseCleared, [16]);
  assert.equal(f.events.at(-1), 'removed');
});

for (const [name, kill] of [['returns nothing', () => undefined], ['returns true', () => true],
  ['throws', () => { throw new Error('kill failed'); }]]) {
  test(`a forced kill that ${name} without an actual close still fails at the deadline (#842)`, async () => {
    const f = lateFixture();
    f.chrome.kill = () => { f.events.push('chrome-request'); f.chrome.killed = true; return kill(); };
    const closing = closeDemoResources(f.resources, f.options);
    await f.tick(); f.serverAck(); await f.tick();
    f.phase(); await f.tick(); f.timeout();
    await assert.rejects(closing, error => error instanceof AggregateError &&
      error.message === 'demo cleanup incomplete' &&
      error.errors.some(item => item.message.startsWith('demo cleanup did not close: chrome-stdio')) &&
      (name !== 'throws' || error.errors.some(item => item.message === 'kill failed')));
    assert.equal(kills(f), 1, 'the deadline does not kill again after the forced phase');
    assert.equal(f.events.includes('removed'), false);
    assert.equal(f.rows.some(row => row.stage === 'closed'), false);
  });
}

test('an observed exit with a still open stdio pipe stays a failure, as in #832', async () => {
  const f = lateFixture();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); await f.tick();
  f.phase();
  f.chrome.exitCode = 1; f.chrome.emit('exit', 1, null); f.socketAck(); await f.tick();
  f.timeout();
  await assert.rejects(closing, error => error instanceof AggregateError &&
    error.errors.some(item => item.message.startsWith('demo cleanup did not close: chrome-stdio')));
  assert.equal(f.events.includes('removed'), false);
  assert.equal(f.rows.some(row => row.stage === 'closed'), false);
});

test('a graceful close inside its part of the budget never kills the owned child', async () => {
  const f = lateFixture();
  const closing = closeDemoResources(f.resources, f.options);
  await f.tick(); f.serverAck(); f.chromeAck(); f.socketAck(); await closing;
  assert.equal(kills(f), 0);
  assert.deepEqual(f.rows.map(row => row.stage), ['closed']);
  assert.deepEqual(f.phaseCleared, [16], 'the unused forced phase is cleared, so it can never fire later');
  assert.equal(f.events.filter(x => x === 'removed').length, 1);
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

// #842: the owned Chrome's forced close must be observed inside the same 5 s cleanup deadline. Browser.close is
// withheld here (test only) so cleanup takes the forced path; close() rejects unless the actual child stdio,
// socket and server closes arrive in time. Every other real demo keeps the normal graceful path.
test('cleanup forces an owned Chrome whose graceful close never takes effect, within the same deadline',
  {skip: skipReason() || false, timeout: 150000}, async () => {
    const stages = [];
    const d = await openDemo({width: 800, height: 600, testWithholdBrowserClose: true,
      cleanupDiagnostic: (stage, pending, extra) => {
        stages.push(stage);
        console.error('forced cleanup: ' + JSON.stringify({stage, pending, ...(extra || {})}));
      }});
    await d.close();
    assert.deepEqual(stages, ['forced', 'closed']);
  });
