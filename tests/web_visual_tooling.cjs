// Checks for the visual-test tooling itself (#546, remaining review notes of PR #599): the layout snapshot's color
// comparison, the baseline updater's exit status, the virtual page clock, and what the driver reports. No Chrome is
// needed except where a test says so.
const {test} = require('node:test'), assert = require('node:assert/strict');
const {DEFAULT_TOLERANCE, diffView, sameColor, colorChannels} = require('./support/layout_snapshot.cjs');

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
