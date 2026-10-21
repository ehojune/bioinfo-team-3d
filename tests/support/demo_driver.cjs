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
const START_MS = 60000;  // Chrome start budget; the node:test budget of each test covers it

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

function startStaticServer(rootDir) {
  const root = path.resolve(rootDir);
  const types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
    '.svg': 'image/svg+xml', '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.png': 'image/png'};
  const server = http.createServer((req, res) => {
    const filename = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname.slice(1)));
    if (filename !== root && !filename.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
    try {
      res.setHeader('Content-Type', types[path.extname(filename)] || 'application/octet-stream');
      res.end(fs.readFileSync(filename));
    } catch { res.writeHead(404).end(); }
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function removeDir(dir) {
  for (let i = 0; i < 20; i++) {
    try { fs.rmSync(dir, {recursive: true, force: true}); return; } catch (e) { if (i === 19) throw e; await sleep(100); }
  }
}

// A request to close is not an acknowledgement. In particular ChildProcess
// 'exit' can precede closure of inherited stdio; await 'close' before declaring
// the fixture closed or removing its profile. Only these owned handles are used.
async function closeDemoResources({ws, wsClosed, chrome, chromeClosed, server, profile},
  {removeProfile = removeDir, setTimer = setTimeout, clearTimer = clearTimeout,
    diagnostic = (stage, pending) => console.error('demo cleanup: ' + JSON.stringify({stage, pending,
      active: typeof process.getActiveResourcesInfo === 'function' ? process.getActiveResourcesInfo().slice(0, 16) : null}))} = {}) {
  const pending = new Set(), failures = [];
  const report = stage => { try { diagnostic(stage, [...pending]); } catch { /* diagnostic only */ } };
  const closing = (name, request, acknowledgement) => {
    pending.add(name);
    return Promise.resolve().then(request).then(() => acknowledgement).then(() => pending.delete(name))
      .catch(error => { failures.push(error); throw error; });
  };
  const waits = [];
  if (ws) waits.push(closing('websocket', () => ws.close(), wsClosed));
  if (chrome) waits.push(closing('chrome-stdio', () => {
    if (chrome.exitCode === null && chrome.signalCode === null) chrome.kill();
  }, chromeClosed));
  waits.push(closing('http-server', () => new Promise((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
  })));
  let timer;
  try {
    await Promise.race([Promise.allSettled(waits), new Promise((_, reject) => {
      timer = setTimer(() => {
        report('timeout');
        reject(new AggregateError([...failures, new Error('demo cleanup did not close: ' + [...pending].join(', '))],
          'demo cleanup incomplete'));
      }, 5000);
    })]);
  } finally { clearTimer(timer); }
  if (failures.length) throw new AggregateError(failures, 'demo cleanup failed');
  await removeProfile(profile);
  report('closed');
}

// Diagnostic only: no timer, transport, expressions, URLs or success receipts.
// A disabled/broken observer must not change browser behavior or mask errors.
function demoLifecycleTrace(emit, clock = () => performance.now()) {
  let origin, previous, count = 0;
  return stage => {
    if (typeof emit !== 'function' || count >= 16) return;
    count++;
    try {
      const now = clock();
      origin ??= now;
      const row = {stage, elapsed_ms: Math.round(now - origin),
        since_previous_ms: previous === undefined ? 0 : Math.round(now - previous)};
      previous = now;
      emit(row);
    } catch { /* diagnostic only */ }
  };
}

// opts: {width, height, reducedMotion=true, colorScheme='light', web=WEB_ROOT, layoutFont=false, fontFamily=null, deadlineMs=60000, diagnostic=null}
// layoutFont forces the metrics-only font from test_font.cjs so text wraps the same on every OS.
async function openDemo(opts = {}) {
  const trace = demoLifecycleTrace(opts.diagnostic);
  trace('open-start');
  const chromePath = findChrome();
  if (!chromePath) throw new Error('no Chrome found');
  const {width = 1440, height = 900, reducedMotion = true, colorScheme = 'light', web = WEB_ROOT, layoutFont = false, fontFamily = null} = opts;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'labhq-visual-'));
  const server = await startStaticServer(web);
  const port = server.address().port;
  if (FORBIDDEN_PORTS.includes(port)) throw new Error('static server landed on a reserved port ' + port);
  const origin = `http://127.0.0.1:${port}`;
  let chrome, ws, chromeClosed, wsClosed, closing;
  const close = () => closing ||= closeDemoResources({ws, wsClosed, chrome, chromeClosed, server, profile});
  try {
    // Chrome resolves its default profile before comparing --user-data-dir; keep both under this temp root.
    const home = path.join(profile, 'home'), local = path.join(home, 'AppData', 'Local'), roaming = path.join(home, 'AppData', 'Roaming');
    fs.mkdirSync(path.join(local, 'Google', 'Chrome', 'User Data'), {recursive: true});
    fs.mkdirSync(roaming, {recursive: true});
    const config = path.join(home, '.config');
    fs.mkdirSync(path.join(config, 'google-chrome'), {recursive: true});
    let chromeError = '';
    chrome = spawn(chromePath, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--force-color-profile=srgb',
      `--lang=${process.env.LABHQ_TEST_LOCALE || 'ko-KR'}`,
      ...(process.env.LABHQ_TEST_CHROME_ARGS ? process.env.LABHQ_TEST_CHROME_ARGS.split(' ') : []),
      ...(process.env.CI ? ['--no-sandbox'] : []), 'about:blank'],
    {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'], env: {...process.env, HOME: home, USERPROFILE: home,
      HOMEDRIVE: path.parse(home).root.slice(0, 2), HOMEPATH: home.slice(2), LOCALAPPDATA: local, APPDATA: roaming, XDG_CONFIG_HOME: config}});
    chromeClosed = new Promise(resolve => chrome.once('close', resolve));
    chrome.stderr.on('data', chunk => { chromeError = (chromeError + chunk).slice(-2000); });
    // A cold Chrome start on a loaded CI runner can take far longer than 10 s (see tests/web_office_browser.cjs):
    // wait for the DevToolsActivePort file and the page target on a deadline, and stop early if Chrome exits.
    let debug, page, exited = null;
    chrome.on('exit', (code, signal) => { exited = {code, signal}; });
    const startDeadline = Date.now() + START_MS;
    while (!debug && !exited && Date.now() < startDeadline) {
      try { debug = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; } catch { /* not written yet */ }
      if (!debug) await sleep(100);
    }
    if (!debug) throw new Error(`Chrome did not publish its debugging port within ${START_MS / 1000}s (exit: ${JSON.stringify(exited)}): ${chromeError}`);
    while (!page && !exited && Date.now() < startDeadline) {
      try { page = (await (await fetch(`http://127.0.0.1:${debug}/json`)).json()).find(t => t.type === 'page'); } catch { /* not listening yet */ }
      if (!page) await sleep(100);
    }
    if (!page) throw new Error(`Chrome has no page target within ${START_MS / 1000}s (exit: ${JSON.stringify(exited)}): ${chromeError}`);
    // Node 22+ has a global WebSocket; older Nodes use the small client in mini_ws.cjs (LABHQ_FORCE_MINI_WS=1 forces it).
    const Socket = typeof WebSocket !== 'undefined' && !process.env.LABHQ_FORCE_MINI_WS ? WebSocket : MiniWebSocket;
    ws = new Socket(page.webSocketDebuggerUrl);
    wsClosed = new Promise(resolve => { ws.onclose = resolve; });
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = e => reject(new Error('CDP socket failed: ' + (e?.message || e))); });
    let serial = 0;
    const pending = new Map(), external = [], errors = [];
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.method === 'Network.requestWillBeSent' && /^https?:/.test(m.params.request.url) && !m.params.request.url.startsWith(origin + '/')) external.push(m.params.request.url);
      if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
      if (m.id) { const p = pending.get(m.id); pending.delete(m.id); if (p) m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result); }
    };
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const id = ++serial; pending.set(id, {resolve, reject}); ws.send(JSON.stringify({id, method, params})); });
    const evaluate = async expression => {
      const r = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
      return r.result.value;
    };
    await cdp('Page.enable'); await cdp('Network.enable'); await cdp('Runtime.enable');
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
        await advance(stepMs); spent += stepMs;
      }
    }
    // Wait, in real time, for a page-side condition that does not depend on the virtual clock (initial boot).
    async function waitReal(label, expression, deadlineMs = 30000) {
      const started = Date.now();
      for (;;) {
        if (await evaluate(`Boolean(${expression})`)) return;
        if (Date.now() - started > deadlineMs) throw new Error(`timed out waiting for ${label}: ${expression}`);
        await sleep(50);
      }
    }
    async function load(search = '?demo=1') {
      trace('navigate-start');
      await cdp('Page.navigate', {url: `${origin}/index.html${search}`});
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
    return {cdp, evaluate, advance, until, waitReal, load, settle, frames, screenshot, external, errors, close, origin, width, height};
  } catch (e) {
    try { await close(); } catch (cleanup) { throw new AggregateError([e, cleanup], 'demo startup and cleanup failed'); }
    throw e;
  }
}

module.exports = {openDemo, skipReason, findChrome, sleep, WEB_ROOT, CLOCK_SOURCE, closeDemoResources, demoLifecycleTrace};
