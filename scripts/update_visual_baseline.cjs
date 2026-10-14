#!/usr/bin/env node
// Regenerates tests/visual/demo-layout.baseline.json on purpose, after an intended change to the 2D office
// layout. Runs tests/web_visual_demo.cjs in update mode, prints what moved compared with the old baseline,
// and leaves the file for you to review and commit. Needs an installed Chrome (or LABHQ_TEST_CHROME).
//   node scripts/update_visual_baseline.cjs
const {spawnSync} = require('node:child_process');
const path = require('node:path');

const test = path.resolve(__dirname, '../tests/web_visual_demo.cjs');
const result = spawnSync(process.execPath, [test], {stdio: 'inherit', env: {...process.env, LABHQ_UPDATE_VISUAL_BASELINE: '1'}});
if (result.status === 0) console.log('\nReview the differences above, then commit tests/visual/demo-layout.baseline.json.');
process.exit(result.status === null ? 1 : result.status);
