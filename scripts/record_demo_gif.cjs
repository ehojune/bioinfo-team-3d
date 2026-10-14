#!/usr/bin/env node
// Re-records the README demo animation (dev-only; needs an installed Chrome, Node 22+, and Python 3).
//
//   node scripts/record_demo_gif.cjs [--out docs/media/office-25d.gif] [--still docs/media/office-25d.png]
//                                    [--frames-dir DIR] [--max-mb 5]
//
// Drives labhq/web/index.html?demo=1 in headless Chrome at 1440x900 on the same virtual page clock the visual
// test uses, so every run takes the same steps: start -> request -> staff working -> decision card -> approve ->
// report ready. Each step is a PNG screenshot scaled to 960x600; scripts/gif_encode.py (standard library only)
// joins them into a looping GIF. The decision-card frame is also saved as the still image the README links.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawnSync} = require('node:child_process');
const {openDemo, skipReason} = require('../tests/support/demo_driver.cjs');

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const option = (name, fallback) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : fallback; };
const OUT = path.resolve(ROOT, option('--out', 'docs/media/office-25d.gif'));
const STILL = path.resolve(ROOT, option('--still', 'docs/media/office-25d.png'));
const MAX_MB = option('--max-mb', '5');
const SIZE = {width: 1440, height: 900, scale: 960 / 1440};

const active = agent => `[...__labhq.S.activeTasks.values()].some(t => t.agent_id === '${agent}')`;
const APPROVAL = "[...__labhq.S.approvals.values()].some(a => a.kind === 'hpc_submit')";
const DONE = "[...__labhq.S.requests.values()].some(r => r.status === 'done')";

function python() {
  if (process.env.LABHQ_PYTHON) return process.env.LABHQ_PYTHON;
  const venv = path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return fs.existsSync(venv) ? venv : (process.platform === 'win32' ? 'python' : 'python3');
}

(async () => {
  const reason = skipReason();
  if (reason) { console.error('cannot record: ' + reason); process.exit(2); }
  const framesDir = path.resolve(option('--frames-dir', fs.mkdtempSync(path.join(os.tmpdir(), 'labhq-demo-frames-'))));
  fs.mkdirSync(framesDir, {recursive: true});
  const d = await openDemo({width: SIZE.width, height: SIZE.height, reducedMotion: true});
  const frames = [];
  const grab = async (name, ms) => {
    await d.frames();
    const file = `${String(frames.length + 1).padStart(2, '0')}-${name}.png`;
    fs.writeFileSync(path.join(framesDir, file), await d.screenshot({x: 0, y: 0, width: SIZE.width, height: SIZE.height, scale: SIZE.scale}));
    frames.push({png: file, ms, name});
  };
  const tab = async name => { await d.evaluate(`document.querySelector('[data-tab-target="${name}"]').click()`); await d.frames(); };
  try {
    await d.load();
    await grab('start', 1100);
    await d.until('request created', '__labhq.S.requests.size > 0');
    await tab('tasks');  // the PI opens the task board to watch the request
    await grab('request', 900);
    await d.until('CSO planning', active('cso'));
    await grab('plan', 900);
    await d.until('staff working', active('biologist'));
    await grab('working', 1000);
    await d.until('decision waiting', APPROVAL);
    await d.advance(2800);  // the "approval requested" toast hides itself
    await tab('decisions');
    await grab('decision', 2200);
    await d.evaluate("document.querySelector('#approvals .decision-card[data-kind=\"hpc_submit\"] .decision-primary').click()");
    await grab('approved', 900);
    await tab('tasks');
    for (const [agent, name] of [['bioinfo-agent', 'qc-pipeline'], ['analyst', 'analysis'], ['qc_reviewer', 'qc-review']]) {
      await d.until(agent, active(agent), {maxVirtualMs: 100000});
      await grab(name, 700);
    }
    await d.until('report ready', DONE, {maxVirtualMs: 200000});
    await tab('decisions');
    await grab('report-ready', 1600);
    await d.evaluate("document.querySelector('#review-cards .decision-primary').click()");
    await grab('report-open', 2400);
    if (d.errors.length || d.external.length) throw new Error('page errors or external requests: ' + JSON.stringify({errors: d.errors, external: d.external}));
  } finally {
    await d.close();
  }
  fs.writeFileSync(path.join(framesDir, 'frames.json'), JSON.stringify({frames: frames.map(({png, ms}) => ({png, ms}))}, null, 1));
  fs.mkdirSync(path.dirname(OUT), {recursive: true});
  const run = spawnSync(python(), [path.join(__dirname, 'gif_encode.py'), path.join(framesDir, 'frames.json'), OUT, '--max-mb', MAX_MB], {stdio: 'inherit'});
  if (run.status !== 0) { console.error('GIF encoding failed; frames kept in ' + framesDir); process.exit(run.status || 1); }
  const decision = frames.find(f => f.name === 'decision');
  fs.mkdirSync(path.dirname(STILL), {recursive: true});
  fs.copyFileSync(path.join(framesDir, decision.png), STILL);
  console.log(`still: ${path.relative(ROOT, STILL)}  (frames: ${framesDir})`);
  if (!option('--frames-dir', '')) fs.rmSync(framesDir, {recursive: true, force: true});
})().catch(error => { console.error(error); process.exit(1); });
