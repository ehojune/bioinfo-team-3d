const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Readiness R16 (2026-10-08): with the research lane on, a metadata table went through CP1. The PI now picks
// 자동 판단 / 간단한 일 / 연구 next to 보내기, and research cannot go to one staff member directly.
const html = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'labhq/web/index.html'), 'utf8');
assert.match(html, /<select id="work-kind"[^>]*>\s*<option value="auto">자동 판단<\/option><option value="simple">간단한 일<\/option><option value="research">연구<\/option>/);
assert.match(html, /work_kind: workKind/);
assert.match(html, /if \(target && workKind === 'research'\)/);
assert.match(html, /\$\('#work-kind'\)\.hidden = noteMode;/);
assert.match(html, /\$\('#work-kind'\)\.value = 'auto';/);
console.log('work kind web tests passed');
