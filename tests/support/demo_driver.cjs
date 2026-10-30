// Headless Chrome driver for the scripted web demo (labhq/web/index.html?demo=1).
// Shared by tests/web_visual_demo.cjs and scripts/record_demo_frames.cjs. No npm package, no download:
// it speaks CDP over the WebSocket built into Node and uses an installed Chrome.
//
// The demo normally advances with wall-clock timers. This driver replaces the page clock (Date, setTimeout,
// setInterval) with a virtual one, so a state such as "decision waiting" is reached by advancing virtual
// time until a DOM condition holds. Nothing here sleeps for a fixed time and every run produces the same events.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), http = require('node:http');
const {spawn} = require('node:child_process');
const {buildTestFont, fontInjectionSource} = require('./test_font.cjs');
const {MiniWebSocket} = require('./mini_ws.cjs');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const WEB_ROOT = process.env.LABHQ_WEB_TEST_ROOT || path.resolve(__dirname, '../../labhq/web');
const FORBIDDEN_PORTS = [8787, 55921];
// #1072: the OS picks the static server's port, and one in 16,384 draws is 55921. That draw is closed and made again
// instead of failing the test; only this many reserved draws in a row are refused (see openDemo).
const STATIC_SERVER_TRIES = 8;
const START_MS = 60000;  // Chrome start budget; the node:test budget of each test covers it
// #833: a navigation still waiting this long reports, once, whether the static server has seen and answered the page
// request. Diagnostic only; it neither ends nor extends the wait.
const NAVIGATE_NOTICE_MS = 5000;
// One cleanup deadline. The graceful Browser.close may use its first part; the rest is for the owned child's forced
// close to be observed (#842: a graceful wait that took the whole budget left the kill no time to show its close).
const CLEANUP_MS = 5000, GRACEFUL_CLEANUP_MS = 3000;
// Profile removal after a close: up to 20 tries, 100 ms apart (removeDir).
const REMOVE_TRIES = 20, REMOVE_RETRY_MS = 100;
// #833 opt-in `workDeadline`: what one close needs once the work stops, i.e. its cleanup deadline, the profile removal
// retries and the process exit. A caller under an outer watchdog ends its work this much earlier, so the owned close
// can still finish before the watchdog kills the process and skips it.
const CLOSE_RESERVE_MS = CLEANUP_MS + REMOVE_TRIES * REMOVE_RETRY_MS + 1000;
// Windows only (#842): Chrome and every descendant run inside one KILL_ON_CLOSE Job. windows_job_browser.py reuses
// the product's Job class (labhq/runner/memory_limit.py) and joins itself to the Job before starting Chrome with its
// own stdin/stdout/stderr, so Chrome's stderr (and the stdio close proof) still reach this driver. The owned child is
// that wrapper; ending it closes the Job, so the kernel ends the browser tree. Nothing is killed by name, tree walk
// or PID. The limit is far above a demo browser: the Job is for ownership, not a memory cap.
const WINDOWS_JOB_WRAPPER = path.resolve(__dirname, 'windows_job_browser.py');
const WINDOWS_JOB_LIMIT_BYTES = 64 * 1024 ** 3;
// #833: the wrapper's fixed stderr line once its Job exists, right before it starts Chrome (windows_job_browser.py).
// Recognized only as the stream's first line; it is a diagnostic and never decides whether a start passed.
const JOB_STARTING_BROWSER = 'LABHQ_JOB_STARTING_BROWSER';
// #833: the wrapper's second fixed line, written once Chrome's CreateProcess has returned (windows_job_browser.py).
// JOB_STARTING_BROWSER only shows that the Job exists. Recognized only as the line right after it; if Chrome's own
// output comes first, it is simply not seen. A diagnostic only, like the first line.
const BROWSER_CREATED = 'LABHQ_BROWSER_CREATED';
// #833: a Chrome start still without DevToolsActivePort this long reports, once, whether Chrome is visibly working
// (entries in its profile, stderr bytes). Diagnostic only; it neither ends nor extends the 60 s start budget.
const CHROME_NOTICE_MS = 5000;

// #833: Chrome's stderr with the wrapper's fixed lines taken off the front. Each counts only in its place (the first
// line, then the line right after it), so the same text later in Chrome's own output is just output. Only counts
// leave this: `chromeBytes()`, `marked()`, `created()`.
function chromeStderrWatcher(onMarker = () => {}, onCreated = () => {}) {
  const markers = [[JOB_STARTING_BROWSER, onMarker], [BROWSER_CREATED, onCreated]];
  let head = '', text = '', bytes = 0, seen = 0, plain = false;
  const chrome = part => {
    if (!part) return;
    bytes += typeof Buffer === 'function' ? Buffer.byteLength(part, 'utf8') : part.length;
    text = (text + part).slice(-2000);
  };
  return {
    feed(chunk) {
      const part = String(chunk);
      if (plain || seen === markers.length) return chrome(part);
      head += part;
      while (seen < markers.length) {
        const [marker, observe] = markers[seen];
        const line = head.match(/^([^\r\n]*)\r?\n/);
        if (!line) {
          // Still possibly the marker line: its text, then CR LF or LF, may arrive split anywhere (e.g. CR | LF).
          if ((marker + '\r\n').startsWith(head) || (marker + '\n').startsWith(head)) return;
          plain = true;
          break;
        }
        if (line[1] !== marker) { plain = true; break; }
        seen++;
        try { observe(); } catch { /* diagnostic only */ }
        head = head.slice(line[0].length);
      }
      chrome(head);
      head = '';
    },
    text: () => text + head,
    chromeBytes: () => bytes,
    marked: () => seen >= 1,
    created: () => seen >= 2,
  };
}

// #833: how many top-level entries the temp profile has (the driver creates one, `home`); null if unreadable.
function profileEntryCount(dir) {
  try { return fs.readdirSync(dir).length; } catch { return null; }
}

// Failure-only #833 snapshot. Count at most 96 entries and descend at most two levels; never print names or contents.
// Unlike the root count at the five-second notice, this can tell whether Chrome wrote inside its profile at all.
function profileActivity(dir, openDir = fs.opendirSync) {
  const pending = [{dir, depth: 0}];
  let entries = 0, files = 0, directories = 0, truncated = false;
  while (pending.length) {
    const current = pending.shift();
    let opened;
    try {
      opened = openDir(current.dir);
      for (;;) {
        if (entries === 96) { truncated = true; break; }
        const child = opened.readSync();
        if (child === null) break;
        entries++;
        if (child.isFile()) files++;
        if (child.isDirectory()) {
          directories++;
          if (current.depth < 2) pending.push({dir: path.join(current.dir, child.name), depth: current.depth + 1});
        }
      }
    } catch { return {entries, files, directories, truncated, unreadable: true}; }
    finally { try { opened?.closeSync(); } catch { /* diagnostic only */ } }
    if (truncated) break;
  }
  return {entries, files, directories, truncated, unreadable: false};
}

// #833: why the DevToolsActivePort reads have found no port yet, as counts per fixed code (`empty`: the file exists but
// has no port line yet). Never the path, message or file content.
const PORT_READ_CODES = ['ENOENT', 'EBUSY', 'EACCES', 'EPERM'];
function portReadCounter() {
  const counts = {};
  const add = code => { counts[code] = (counts[code] || 0) + 1; };
  return {
    failed: error => add(PORT_READ_CODES.includes(error?.code) ? error.code : 'other'),
    empty: () => add('empty'),
    counts: () => ({...counts}),
  };
}

// The interpreter is named explicitly (tests/test_web_3d.py passes sys.executable). No PATH lookup and no Store
// alias: a Chrome started without the owned Job would hide the very cleanup this driver must prove.
function windowsJobPython(platform = process.platform, env = process.env) {
  if (platform !== 'win32') return null;
  const python = env.LABHQ_TEST_PYTHON;
  if (typeof python !== 'string' || !path.win32.isAbsolute(python) || /[\\/]WindowsApps[\\/]/i.test(python)
      || !fs.existsSync(python)) {
    throw new Error('LABHQ_TEST_PYTHON must name an absolute, existing Python (not a Store alias) for the owned '
      + 'Windows Job wrapper');
  }
  if (!fs.existsSync(WINDOWS_JOB_WRAPPER)) throw new Error('owned Windows Job wrapper is missing: ' + WINDOWS_JOB_WRAPPER);
  return python;
}

function browserCommand(chromePath, args, python) {
  if (!python) return [chromePath, args];
  return [python, ['-I', WINDOWS_JOB_WRAPPER, '--windows-job', String(WINDOWS_JOB_LIMIT_BYTES), '--', chromePath, ...args]];
}

function findChrome() {
  const candidates = [process.env.LABHQ_TEST_CHROME,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  return candidates.find(p => p && fs.existsSync(p)) || null;
}

// Why the browser check cannot run here, or '' when it can.
function skipReason() {
  if (typeof fetch === 'undefined') return 'this Node has no global fetch (Node 18+ needed for the DevTools driver)';
  if (!findChrome()) return 'no Chrome found (set LABHQ_TEST_CHROME or install Google Chrome/Chromium)';
  return '';
}

// Runs inside the page before any page script. Seeded randomness keeps cost numbers identical.
const CLOCK_SOURCE = `(() => {
  if (window.__vclock) return;
  try { localStorage.clear(); } catch (e) {}
  const RealDate = Date, base = 1792000000000;
  const realSetTimeout = window.setTimeout.bind(window), realClear = window.clearTimeout.bind(window);
  let t = 0, seq = 0;
  const timers = new Map();
  const channel = new MessageChannel();
  const macrotask = () => new Promise(r => { channel.port1.onmessage = () => r(); channel.port2.postMessage(0); });
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length) super(...a); else super(base + t); }
    static now() { return base + t; }
  }
  window.Date = FakeDate;
  const add = (fn, ms, args, every) => {
    const id = ++seq, delay = Math.max(0, Number(ms) || 0);
    timers.set(id, {id, at: t + delay, fn: typeof fn === 'function' ? fn : () => {}, args, every: every ? Math.max(1, delay) : 0});
    return id;
  };
  // A real browser reports an exception thrown by a timer callback as uncaught (window.onerror and the DevTools
  // Runtime.exceptionThrown event, which the driver collects in its errors list) and carries on with the other timers.
  // reportError does the same; logging it with console.error would hide it from assert.deepEqual(d.errors, []).
  const reportUncaught = e => { try { window.reportError(e); } catch (_) { console.error(e); } };
  window.setTimeout = (fn, ms, ...args) => add(fn, ms, args, false);
  window.setInterval = (fn, ms, ...args) => add(fn, ms, args, true);
  window.clearTimeout = window.clearInterval = id => { timers.delete(id); };
  let r = 12345;
  Math.random = () => { r = (r * 1664525 + 1013904223) % 4294967296; return r / 4294967296; };
  const frames = () => new Promise(res => {
    let finished = false;
    const done = () => { if (!finished) { finished = true; realClear(guard); res(); } };
    // A hidden tab may never paint; the real-time guard keeps the run from hanging on it.
    const guard = realSetTimeout(done, 1500);
    requestAnimationFrame(() => requestAnimationFrame(done));
  });
  async function advance(ms) {
    const target = t + ms;
    for (;;) {
      let next = null;
      for (const x of timers.values()) if (x.at <= target && (!next || x.at < next.at || (x.at === next.at && x.id < next.id))) next = x;
      if (!next) break;
      t = Math.max(t, next.at);
      if (next.every) next.at += next.every; else timers.delete(next.id);
      try { next.fn(...next.args); } catch (e) { reportUncaught(e); }
      await macrotask();
    }
    t = target;
    await macrotask();
  }
  window.__vclock = {advance, frames, now: () => t, pending: () => timers.size};
})();`;

function startStaticServer(rootDir, observe = null) {
  const root = path.resolve(rootDir);
  const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
    '.svg': 'image/svg+xml', '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.png': 'image/png'};
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    try { observe?.(pathname, res); } catch { /* diagnostic only */ }
    const filename = path.resolve(root, decodeURIComponent(pathname.slice(1)));
    if (filename !== root && !filename.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
      res.setHeader('Content-Type', types[path.extname(filename)] || 'application/octet-stream');
      res.end(fs.readFileSync(filename));
    } catch { res.writeHead(404).end(); }
  });
  const listen = () => new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.removeListener('error', reject);
      resolve();
    });
  });
  const close = () => new Promise(resolve => server.close(error => resolve(error || null)));
  return (async () => {
    for (let attempt = 1; ; attempt++) {
      await listen();
      // A reserved draw is closed and drawn again. On the last draw, or when the close itself fails, the server is
      // handed over as it is: the caller refuses the port and its own close reports the failure (both errors kept).
      if (!FORBIDDEN_PORTS.includes(server.address().port) || attempt === STATIC_SERVER_TRIES) return server;
      if (await close()) return server;
    }
  })();
}

async function removeDir(dir) {
  for (let i = 0; i < REMOVE_TRIES; i++) {
    try { fs.rmSync(dir, {recursive: true, force: true}); return; } catch (e) { if (i === REMOVE_TRIES - 1) throw e; await sleep(REMOVE_RETRY_MS); }
  }
}

// A request to close is not an acknowledgement. In particular ChildProcess
// 'exit' can precede closure of inherited stdio; await 'close' before declaring
// the fixture closed or removing its profile. Only these owned handles are used.
// On Windows kill() is abrupt. Let the owned browser shut down its children
// before closing its CDP transport; never replace the stdio acknowledgement.
function requestBrowserShutdown(chrome, requestBrowserClose, failures) {
  // A usable owned CDP connection can outlast the launcher's exit event.
  try {
    if (requestBrowserClose?.() === true) return true;
  } catch (error) { failures.push(error); }
  if (chrome.exitCode === null && chrome.signalCode === null) chrome.kill();
  return false;
}

async function closeDemoResources({ws, wsClosed, chrome, chromeClosed, requestBrowserClose, server, profile},
  {removeProfile = removeDir, setTimer = setTimeout, clearTimer = clearTimeout, clock = () => performance.now(),
    diagnostic = (stage, pending, extra = {}) => console.error('demo cleanup: ' + JSON.stringify({stage, pending, ...extra,
      active: typeof process.getActiveResourcesInfo === 'function' ? process.getActiveResourcesInfo().slice(0, 16) : null,
      chrome_exit_observed: chrome ? chrome.exitCode !== null || chrome.signalCode !== null : null,
      // Which client closed the CDP socket and on which Node: a close that never reports differs by implementation (#1107).
      node_version: process.version, ws_kind: ws ? (ws.constructor?.name === 'MiniWebSocket' ? 'mini' : 'global') : null,
      stderr_closed: typeof chrome?.stderr?.closed === 'boolean' ? chrome.stderr.closed : null}))} = {}) {
  const pending = new Set(), failures = [];
  let graceful = false, socketCloseRequested = false;
  const closeSocket = () => {
    if (!socketCloseRequested) { socketCloseRequested = true; ws.close(); }
  };
  const report = (stage, extra) => { try { diagnostic(stage, [...pending], extra); } catch { /* diagnostic only */ } };
  // After the deadline, record which owned acknowledgements still arrive and when (#842). Observation only: no
  // timer or handle is added, the rejection stands, and a late event never removes the profile or marks closure.
  const acknowledged = {chrome: false, socket: false};
  if (chrome && chromeClosed) chromeClosed.then(() => { acknowledged.chrome = true; }, () => {});
  if (ws && wsClosed) wsClosed.then(() => { acknowledged.socket = true; }, () => {});
  const observeLate = () => {
    let deadline;
    try { deadline = clock(); } catch { return; }
    const late = stage => report(stage, {after_deadline_ms: Math.round(clock() - deadline)});
    if (chrome && chrome.exitCode === null && chrome.signalCode === null && typeof chrome.once === 'function') {
      chrome.once('exit', () => late('late-chrome-exit'));
    }
    if (chrome && chromeClosed && !acknowledged.chrome) chromeClosed.then(() => late('late-chrome-stdio-close'), () => {});
    if (ws && wsClosed && !acknowledged.socket) wsClosed.then(() => late('late-socket-close'), () => {});
  };
  const closing = (name, request, acknowledgement) => {
    pending.add(name);
    return Promise.resolve().then(request).then(() => acknowledgement).then(() => pending.delete(name))
      .catch(error => { failures.push(error); throw error; });
  };
  const waits = [];
  if (chrome) waits.push(closing('chrome-stdio', () => {
    graceful = requestBrowserShutdown(chrome, requestBrowserClose, failures);
  }, chromeClosed));
  if (ws) waits.push(closing('websocket', () => graceful ? chromeClosed.then(closeSocket) : closeSocket(), wsClosed));
  if (server) waits.push(closing('http-server', () => new Promise((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
  })));
  // A stalled graceful close ends the owned child and its CDP socket once. Only the same actual close, socket and
  // server acknowledgements then complete cleanup; a kill request or its return value never counts as closed.
  let forced = false;
  const force = () => {
    if (!graceful || forced) return;
    forced = true;
    report('forced');
    try { requestBrowserShutdown(chrome, undefined, failures); } catch (error) { failures.push(error); }
    if (ws) { try { closeSocket(); } catch (error) { failures.push(error); } }
  };
  let timer, phase;
  try {
    await Promise.race([Promise.allSettled(waits), new Promise((_, reject) => {
      // Only an acquired owned Chrome has a forced phase; a partial startup keeps the single deadline (#843 CI).
      if (chrome) phase = setTimer(force, GRACEFUL_CLEANUP_MS);
      timer = setTimer(() => {
        report('timeout');
        try { observeLate(); } catch { /* diagnostic only */ }
        // If the forced phase never ran, keep the owned-child/transport attempt. The deadline STILL rejects.
        force();
        reject(new AggregateError([...failures, new Error('demo cleanup did not close: ' + [...pending].join(', '))],
          'demo cleanup incomplete'));
      }, CLEANUP_MS);
    })]);
  } finally { if (phase !== undefined) clearTimer(phase); clearTimer(timer); }
  if (failures.length) throw new AggregateError(failures, 'demo cleanup failed');
  await removeProfile(profile);
  report('closed');
}

// Diagnostic only: no timer, transport, expressions, URLs or success receipts.
// A disabled/broken observer must not change browser behavior or mask errors.
function demoLifecycleTrace(emit, clock = () => performance.now()) {
  let origin, previous, count = 0, observed = 0, finished = false;
  const row = (stage, extra, observation) => {
    if (typeof emit !== 'function' || (observation ? observed >= 6 : count >= 16)) return;
    if (observation) observed++; else count++;
    try {
      const now = clock();
      origin ??= now;
      const value = {stage, elapsed_ms: Math.round(now - origin),
        since_previous_ms: previous === undefined ? 0 : Math.round(now - previous), ...extra};
      if (!observation) previous = now;
      emit(value);
    } catch { /* diagnostic only */ }
  };
  const trace = (stage, extra) => row(stage, extra, false);
  // #833: what happened inside a boundary (the static server saw the page request, a navigation is still waiting).
  // Its own small budget, so it never displaces a lifecycle row, and measured from the last boundary without moving it.
  trace.observe = (stage, extra) => row(stage, extra, true);
  // #833: one last row where a session's work stopped (the work deadline). Outside both budgets, so a full trace still
  // shows it, and never more than once.
  trace.final = (stage, extra) => {
    if (finished || typeof emit !== 'function') return;
    finished = true;
    try {
      const now = clock();
      origin ??= now;
      emit({stage, elapsed_ms: Math.round(now - origin),
        since_previous_ms: previous === undefined ? 0 : Math.round(now - previous), ...extra});
    } catch { /* diagnostic only */ }
  };
  return trace;
}

// #833 opt-in: one absolute work deadline (epoch ms, `Date.now()`) that several openDemo sessions can share. Without
// one (`deadline` undefined) nothing changes: `bound` runs the wait as before and starts no timer. With one, a wait still
// pending at the deadline is ended, not merely abandoned: its AbortSignal fires (fetch, response body) and `onExpire`
// lets the caller drop what it holds (a pending CDP call). Resources it owns (Chrome, socket, server, profile) are left
// to the session's own close(), which the caller runs in the time it reserved after the deadline (CLOSE_RESERVE_MS).
// `onExpired(stage)` sees the first expiry only.
function workBudget(deadline, {now = () => Date.now(), setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = id => clearTimeout(id), onExpired = () => {}, phase = 'work'} = {}) {
  const limited = Number.isFinite(deadline);
  let reported = false;
  const remaining = () => limited ? deadline - now() : Infinity;
  const expired = stage => {
    if (!reported) { reported = true; try { onExpired(stage); } catch { /* diagnostic only */ } }
    return Object.assign(new Error(`demo ${phase} deadline reached during ${stage}`),
      {code: 'DEMO_WORK_DEADLINE', stage, phase});
  };
  const check = stage => { if (remaining() <= 0) throw expired(stage); };
  const bound = (stage, start, onExpire = () => {}) => {
    if (!limited) return start(undefined);
    return new Promise((resolve, reject) => {
      const left = remaining();
      if (left <= 0) { reject(expired(stage)); return; }
      const controller = new AbortController();
      let settled = false;
      const timer = setTimer(() => {
        if (settled) return;
        settled = true;
        try { controller.abort(); } catch { /* the rejection below still stands */ }
        try { onExpire(); } catch { /* the rejection below still stands */ }
        reject(expired(stage));
      }, Math.min(left, 2 ** 31 - 1));
      const finish = settle => value => {
        if (settled) return;  // a late result after the deadline is dropped
        settled = true;
        clearTimer(timer);
        settle(value);
      };
      Promise.resolve().then(() => start(controller.signal)).then(finish(resolve), finish(reject));
    });
  };
  // The latest time a loop with its own budget may still run: that budget, or the work deadline if earlier.
  const cap = time => limited ? Math.min(time, deadline) : time;
  return {limited, remaining, check, bound, cap, expired};
}

// opts: {width, height, reducedMotion=true, colorScheme='light', web=WEB_ROOT, layoutFont=false, fontFamily=null, deadlineMs=60000, diagnostic=null, workDeadline, startupBudgetMs, uiBudgetMs}
// layoutFont forces the metrics-only font from test_font.cjs so text wraps the same on every OS.
// workDeadline (#833, opt-in): an absolute Date.now() time after which this session starts no new work and every wait
// fails (see workBudget). deadlineMs stays the per-call guard of until(); both apply.
// Test-only: testWithholdBrowserClose reports Browser.close as sent without sending it, so cleanup must take the
// forced path; cleanupDiagnostic receives the cleanup stages instead of stderr.
async function openDemo(opts = {}) {
  const trace = demoLifecycleTrace(opts.diagnostic);
  trace('open-start');
  // The mandatory resizable test opts into two bounded phases. The outer work deadline always caps both; it still
  // reserves time for one owned close before the Python watchdog. Other demo callers keep their existing budget.
  const phased = opts.startupBudgetMs !== undefined || opts.uiBudgetMs !== undefined;
  if (phased && !(Number.isFinite(opts.workDeadline) && Number.isFinite(opts.startupBudgetMs)
      && opts.startupBudgetMs > 0 && Number.isFinite(opts.uiBudgetMs) && opts.uiBudgetMs > 0)) {
    throw new Error('phased demo needs an outer work deadline and positive startup/UI budgets');
  }
  const startupDeadline = phased ? Math.min(opts.workDeadline, Date.now() + opts.startupBudgetMs) : undefined;
  const chromePath = findChrome();
  if (!chromePath) throw new Error('no Chrome found');
  const jobPython = windowsJobPython();  // fails closed before any profile, server or browser exists
  const {width = 1440, height = 900, reducedMotion = true, colorScheme = 'light', web = WEB_ROOT, layoutFont = false, fontFamily = null} = opts;
  let portReads = null;
  const phaseBudget = (deadline, phase) => workBudget(deadline, {phase, onExpired: stage =>
    trace.final(`${phase}-deadline`, {during: stage, ...(portReads ? {port_reads: portReads.counts()} : {})})});
  let budget = phaseBudget(phased ? startupDeadline : opts.workDeadline, phased ? 'startup' : 'work');
  budget.check('open-start');  // an exhausted shared budget opens nothing: no profile, server or browser
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'labhq-visual-'));
  trace('profile-ready');
  let server, chrome, ws, chromeClosed, wsClosed, requestBrowserClose, closing;
  let pageRequest = null;  // armed by load() for the page request of that one navigation
  const close = () => closing ||= closeDemoResources({ws, wsClosed, chrome, chromeClosed, requestBrowserClose, server, profile},
    opts.cleanupDiagnostic ? {diagnostic: opts.cleanupDiagnostic} : undefined);
  try {
    server = await startStaticServer(web, (pathname, res) => {
      const watch = pageRequest;
      if (!watch || watch.requested || pathname !== '/index.html') return;
      watch.requested = true;
      trace.observe('server-request');
      res.once('finish', () => { watch.responded = true; trace.observe('server-response', {status: res.statusCode}); });
    });
    trace('server-ready');
    // A loopback listen is not raced (an abandoned listen would leave a server nobody closes); it is checked once it
    // is owned, and close() below ends it.
    budget.check('server-ready');
    const port = server.address().port;
    if (FORBIDDEN_PORTS.includes(port)) throw new Error('static server landed on a reserved port ' + port);
    const origin = `http://127.0.0.1:${port}`;
    // Chrome resolves its default profile before comparing --user-data-dir; keep both under this temp root.
    const home = path.join(profile, 'home'), local = path.join(home, 'AppData', 'Local'), roaming = path.join(home, 'AppData', 'Roaming');
    fs.mkdirSync(path.join(local, 'Google', 'Chrome', 'User Data'), {recursive: true});
    fs.mkdirSync(roaming, {recursive: true});
    const config = path.join(home, '.config');
    fs.mkdirSync(path.join(config, 'google-chrome'), {recursive: true});
    let chromeError = '';
    const [browser, browserArgs] = browserCommand(chromePath, ['--headless=new', '--remote-debugging-port=0',
      `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--force-color-profile=srgb',
      `--lang=${process.env.LABHQ_TEST_LOCALE || 'ko-KR'}`,
      ...(process.env.LABHQ_TEST_CHROME_ARGS ? process.env.LABHQ_TEST_CHROME_ARGS.split(' ') : []),
      ...(process.env.CI ? ['--no-sandbox'] : []), 'about:blank'], jobPython);
    chrome = spawn(browser, browserArgs,
    {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'], env: {...process.env, HOME: home, USERPROFILE: home,
      HOMEDRIVE: path.parse(home).root.slice(0, 2), HOMEPATH: home.slice(2), LOCALAPPDATA: local, APPDATA: roaming, XDG_CONFIG_HOME: config}});
    chromeClosed = new Promise(resolve => chrome.once('close', resolve));
    const stderrWatch = chromeStderrWatcher(() => trace.observe('job-ready'), () => trace.observe('chrome-created'));
    chrome.stderr.on('data', chunk => { stderrWatch.feed(chunk); chromeError = stderrWatch.text(); });
    trace('chrome-spawned');
    // A cold Chrome start on a loaded CI runner can take far longer than 10 s (see tests/web_office_browser.cjs):
    // wait for the DevToolsActivePort file and the page target on a deadline, and stop early if Chrome exits.
    let debug, page, exited = null;
    chrome.on('exit', (code, signal) => { exited = {code, signal}; });
    // #833: the start budget ends no later than the work deadline, if one was given.
    const startDeadline = budget.cap(Date.now() + START_MS);
    const noticeAt = Date.now() + CHROME_NOTICE_MS;
    let noticed = false;
    portReads = portReadCounter();
    while (!debug && !exited && Date.now() < startDeadline) {
      try { debug = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; } catch (error) { portReads.failed(error); }
      if (debug === '') portReads.empty();
      if (!debug && !noticed && Date.now() >= noticeAt) {
        noticed = true;  // #833: once; counts only, no path or stderr text
        trace.observe('chrome-waiting', {job_marker_seen: stderrWatch.marked(), chrome_created_seen: stderrWatch.created(),
          port_reads: portReads.counts(), profile_entries: profileEntryCount(profile),
          chrome_stderr_bytes: stderrWatch.chromeBytes()});
      }
      if (!debug) await sleep(Math.max(0, Math.min(100, startDeadline - Date.now())));
    }
    if (!debug) trace.observe('chrome-start-failed', {
      job_marker_seen: stderrWatch.marked(), chrome_created_seen: stderrWatch.created(),
      port_reads: portReads.counts(), profile: profileActivity(profile),
      chrome_stderr_bytes: stderrWatch.chromeBytes(), chrome_exited: exited !== null,
    });
    if (!debug && !exited) budget.check('chrome-start');
    if (!debug) throw new Error(phased
      ? `Chrome startup did not publish its debugging port (exit: ${JSON.stringify(exited)}): ${chromeError}`
      : `Chrome did not publish its debugging port within ${START_MS / 1000}s (exit: ${JSON.stringify(exited)}): ${chromeError}`);
    trace('debug-port-ready');
    // #833: whether the wait was for DevTools to answer at all or for a page target in its answer.
    const polls = {json_attempts: 0, json_unanswered: 0, json_without_page: 0};
    while (!page && !exited && Date.now() < startDeadline) {
      polls.json_attempts++;
      let targets = null;
      // #833: with a work deadline the request and its body read are aborted at it, not left running.
      try {
        targets = await budget.bound('devtools-targets', async signal =>
          (await fetch(`http://127.0.0.1:${debug}/json`, signal ? {signal} : undefined)).json());
      } catch { /* not listening yet, or aborted at the work deadline (checked below) */ }
      if (targets === null) polls.json_unanswered++;
      else if (!(page = Array.isArray(targets) ? targets.find(t => t.type === 'page') : undefined)) polls.json_without_page++;
      if (!page) await sleep(Math.max(0, Math.min(100, startDeadline - Date.now())));
    }
    if (!page && !exited) budget.check('devtools-targets');
    if (!page) throw new Error(phased
      ? `Chrome startup has no page target (exit: ${JSON.stringify(exited)}): ${chromeError}`
      : `Chrome has no page target within ${START_MS / 1000}s (exit: ${JSON.stringify(exited)}): ${chromeError}`);
    trace('target-ready', polls);
    // Node 22+ has a global WebSocket; older Nodes use the small client in mini_ws.cjs (LABHQ_FORCE_MINI_WS=1 forces it).
    const Socket = typeof WebSocket !== 'undefined' && !process.env.LABHQ_FORCE_MINI_WS ? WebSocket : MiniWebSocket;
    ws = new Socket(page.webSocketDebuggerUrl);
    wsClosed = new Promise(resolve => { ws.onclose = resolve; });
    // The socket is owned from here: a handshake still pending at the work deadline is ended by close() below.
    await budget.bound('cdp-socket', () => new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = e => reject(new Error('CDP socket failed: ' + (e?.message || e))); }));
    trace('socket-ready');
    let serial = 0;
    requestBrowserClose = () => {
      if (ws.closed || (typeof ws.readyState === 'number' && ws.readyState !== 1)) return false;
      if (opts.testWithholdBrowserClose === true) return true;  // a graceful close that never takes effect
      // Do not await a CDP response that browser shutdown itself may prevent.
      // Actual socket and child/stdio close events are the completion proof.
      ws.send(JSON.stringify({id: ++serial, method: 'Browser.close'}));
      return true;
    };
    const pending = new Map(), external = [], errors = [];
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.method === 'Network.requestWillBeSent' && /^https?:/.test(m.params.request.url) && !m.params.request.url.startsWith(origin + '/')) external.push(m.params.request.url);
      if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
      if (m.id) { const p = pending.get(m.id); pending.delete(m.id); if (p) m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result); }
    };
    // #833: with a work deadline a call still unanswered at it is dropped from `pending` (a late reply is ignored) and
    // fails; the browser and socket are ended by close().
    const cdp = (method, params = {}) => {
      const id = ++serial;
      return budget.bound(method, () => new Promise((resolve, reject) => { pending.set(id, {resolve, reject}); ws.send(JSON.stringify({id, method, params})); }),
        () => pending.delete(id));
    };
    const evaluate = async expression => {
      const r = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
      return r.result.value;
    };
    await cdp('Page.enable'); await cdp('Network.enable'); await cdp('Runtime.enable');
    trace('protocol-ready');
    await cdp('Page.addScriptToEvaluateOnNewDocument', {source: CLOCK_SOURCE});
    if (layoutFont) await cdp('Page.addScriptToEvaluateOnNewDocument', {source: fontInjectionSource(buildTestFont().toString('base64'))});
    if (fontFamily) {
      // Stress hook for tolerance checks: replace the whole UI font stack with another family.
      await cdp('Page.addScriptToEvaluateOnNewDocument', {source: `document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');s.textContent=':root,body,button,input,select,textarea{font-family:${fontFamily} !important}';document.head.appendChild(s);});`});
    }
    // Same dates and number formats on every machine (LABHQ_TEST_LOCALE / LABHQ_TEST_TZ are stress hooks).
    await cdp('Emulation.setTimezoneOverride', {timezoneId: process.env.LABHQ_TEST_TZ || 'Asia/Seoul'}).catch(() => {});
    await cdp('Emulation.setLocaleOverride', {locale: process.env.LABHQ_TEST_LOCALE || 'ko-KR'}).catch(() => {});
    await cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
    await cdp('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: reducedMotion ? 'reduce' : 'no-preference'},
      {name: 'prefers-color-scheme', value: colorScheme}]});

    const frames = () => evaluate('window.__vclock.frames()');
    const advance = ms => evaluate(`window.__vclock.advance(${Number(ms)})`);
    // Advance virtual time in small steps until the page-side expression is truthy. Real time only bounds a hung page.
    async function until(label, expression, {maxVirtualMs = 120000, stepMs = 100} = {}) {
      const started = Date.now();
      let spent = 0;
      for (;;) {
        if (await evaluate(`Boolean(${expression})`)) { await frames(); return spent; }
        if (spent >= maxVirtualMs) throw new Error(`demo never reached "${label}" within ${maxVirtualMs} virtual ms: ${expression}`);
        if (Date.now() - started > (opts.deadlineMs || 60000)) throw new Error(`demo is stuck before "${label}" (real-time guard)`);
        budget.check(label);
        await advance(stepMs); spent += stepMs;
      }
    }
    // Wait, in real time, for a page-side condition that does not depend on the virtual clock (initial boot).
    async function waitReal(label, expression, deadlineMs = 30000) {
      const started = Date.now();
      for (;;) {
        if (await evaluate(`Boolean(${expression})`)) return;
        if (Date.now() - started > deadlineMs) throw new Error(`timed out waiting for ${label}: ${expression}`);
        budget.check(label);
        await sleep(budget.limited ? Math.max(0, Math.min(50, budget.remaining())) : 50);
      }
    }
    async function load(search = '?demo=1') {
      trace('navigate-start');
      const watch = pageRequest = {requested: false, responded: false};
      const notice = setTimeout(() => trace.observe('navigate-waiting',
        {page_requested: watch.requested, page_responded: watch.responded}), NAVIGATE_NOTICE_MS);
      notice.unref?.();
      try {
        await cdp('Page.navigate', {url: `${origin}/index.html${search}`});
      } finally {
        clearTimeout(notice);
        if (pageRequest === watch) pageRequest = null;
      }
      trace('navigate-ready');
      trace('boot-start');
      await waitReal('demo boot', "window.__labhq && window.__vclock && document.querySelectorAll('#zones .office-character').length>=11");
      trace('boot-ready');
      if (layoutFont) {
        const ok = await evaluate('window.__layoutFontLoaded');
        if (!ok) throw new Error('layout test font was rejected by Chrome: ' + await evaluate('window.__layoutFontError'));
        const probe = await evaluate("(()=>{const s=document.createElement('span');s.style.cssText='position:absolute;font:16px LabhqLayoutFont';s.textContent='가';document.body.appendChild(s);const w=s.getBoundingClientRect().width;s.remove();return w})()");
        // FreeType (Linux) rounds advances to 1/64 px, so allow a hair of difference from the exact 16 px.
        if (Math.abs(probe - 16) > 0.1) throw new Error('layout test font is not applied (Hangul probe is ' + probe + 'px, expected 16px)');
      }
      trace('frames-start');
      await frames();
      trace('frames-ready');
    }
    async function settle() {
      await until('animations finished', "document.getAnimations().length===0", {maxVirtualMs: 2000, stepMs: 50}).catch(() => {});
      await frames();
    }
    const screenshot = async (clip = null) => {
      const r = await cdp('Page.captureScreenshot', {format: 'png', fromSurface: true, ...(clip ? {clip} : {})});
      return Buffer.from(r.data, 'base64');
    };
    trace('open-ready');
    if (phased) {
      budget = phaseBudget(Math.min(opts.workDeadline, Date.now() + opts.uiBudgetMs), 'ui');
      budget.check('ui-start');  // if startup spent the outer budget, close what it owns before returning
    }
    return {cdp, evaluate, advance, until, waitReal, load, settle, frames, screenshot, external, errors, close, origin, width, height};
  } catch (e) {
    try { await close(); } catch (cleanup) { throw new AggregateError([e, cleanup], 'demo startup and cleanup failed'); }
    throw e;
  }
}

module.exports = {openDemo, skipReason, findChrome, sleep, WEB_ROOT, CLOCK_SOURCE, closeDemoResources, demoLifecycleTrace, requestBrowserShutdown,
  CLEANUP_MS, GRACEFUL_CLEANUP_MS, WINDOWS_JOB_WRAPPER, WINDOWS_JOB_LIMIT_BYTES, windowsJobPython, browserCommand,
  JOB_STARTING_BROWSER, chromeStderrWatcher, profileEntryCount, profileActivity, BROWSER_CREATED, portReadCounter, workBudget,
  CLOSE_RESERVE_MS};
