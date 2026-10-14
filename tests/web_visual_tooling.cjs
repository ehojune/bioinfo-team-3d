// Checks for the visual-test tooling itself (#546, remaining review notes of PR #599): the layout snapshot's color
// comparison, the baseline updater's exit status, the virtual page clock, and what the driver reports. No Chrome is
// needed except where a test says so.
const {test} = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {DEFAULT_TOLERANCE, diffView, sameColor, colorChannels} = require('./support/layout_snapshot.cjs');
const {updateBaseline} = require('../scripts/update_visual_baseline.cjs');

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

