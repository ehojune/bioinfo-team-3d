#!/usr/bin/env node
// Regenerates tests/visual/demo-layout.baseline.json on purpose, after an intended change to the 2D office
// layout. Runs tests/web_visual_demo.cjs in update mode, prints what moved compared with the old baseline,
// and leaves the file for you to review and commit. Needs an installed Chrome (or LABHQ_TEST_CHROME).
//   node scripts/update_visual_baseline.cjs
// Exits 0 only when the baseline file was rewritten with different content. A run where the test is skipped
// (no Chrome) or the layout already matches the baseline exits 1 and says so: nothing is left to commit.
const {spawnSync} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {skipReason} = require('../tests/support/demo_driver.cjs');

const TEST = path.resolve(__dirname, '../tests/web_visual_demo.cjs');
const BASELINE = path.resolve(__dirname, '../tests/visual/demo-layout.baseline.json');

// Line endings do not count: a Windows checkout may hold the baseline with CRLF while the test writes LF.
const digest = file => {
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n')).digest('hex');
  } catch { return null; }
};

// Options are injectable for tests/web_visual_tooling.cjs. Returns the exit status.
function updateBaseline({test = TEST, baseline = BASELINE, skip = skipReason, run = spawnSync,
  log = console.log, error = console.error} = {}) {
  const reason = skip();
  if (reason) {
    error(`Baseline NOT updated: the visual test would be skipped here (${reason}).`);
    return 1;
  }
  const before = digest(baseline);
  const result = run(process.execPath, [test], {stdio: 'inherit', env: {...process.env, LABHQ_UPDATE_VISUAL_BASELINE: '1'}});
  if (result.status === null || result.status === undefined) {
    error(`Baseline NOT updated: the visual test did not finish (${result.signal || result.error || 'no exit status'}).`);
    return 1;
  }
  if (result.status !== 0) {
    error(`Baseline NOT updated: the visual test failed (exit ${result.status}).`);
    return result.status;
  }
  if (digest(baseline) === before) {
    error(`Baseline NOT updated: ${path.relative(process.cwd(), baseline)} is unchanged ` +
      '(the test was skipped, or the layout already matches it). There is nothing to commit.');
    return 1;
  }
  log('\nReview the differences above, then commit tests/visual/demo-layout.baseline.json.');
  return 0;
}

if (require.main === module) process.exit(updateBaseline());
module.exports = {updateBaseline};
