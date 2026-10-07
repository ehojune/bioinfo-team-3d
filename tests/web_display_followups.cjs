// PR #387 and #389 follow-ups: the 3D request panel shows plan assumptions and the request bundle, and both views
// mark an incomplete bundle next to its path.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', 'labhq/web');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const live3d = fs.readFileSync(path.join(root, 'lab3d/src/live.js'), 'utf8');

// 2.5D: run the real bundleHTML from the page source.
const start = html.indexOf('const bundleHTML = ');
const end = html.indexOf('\nlet reqKey', start);
assert.ok(start > 0 && end > start, 'bundleHTML source found');
const context = vm.createContext({esc: value => String(value).replace(/[&<>"]/g, c => `&#${c.charCodeAt(0)};`)});
vm.runInContext(`${html.slice(start, end)}\nthis.bundleHTML = bundleHTML;`, context);
const complete = context.bundleHTML({bundlePath: 'C:\\runs\\r1', bundleStatus: 'complete'});
assert.match(complete, /요청 묶음: <code>C:\\runs\\r1<\/code>/);
assert.doesNotMatch(complete, /불완전/);
const incomplete = context.bundleHTML({bundlePath: 'C:\\runs\\r1', bundleStatus: 'incomplete'});
assert.match(incomplete, /요청 묶음\(불완전\)/);
assert.match(incomplete, /MANIFEST\.tsv/);
assert.match(context.bundleHTML({bundleWarning: 'disk full'}), /요청 묶음 경고: disk full/);
const warned = context.bundleHTML({bundlePath: 'C:\\runs\\r1', bundleStatus: 'complete', bundleWarning: 'crate missing'});
assert.match(warned, /요청 묶음: <code>C:\\runs\\r1<\/code>/);
assert.match(warned, /요청 묶음 경고: crate missing/);
assert.equal(context.bundleHTML({}), '');
assert.match(html, /q\.bundlePath, q\.bundleStatus, q\.bundleGrade, q\.bundleWarning/, '2.5D re-renders when only the status changes');
assert.match(context.bundleHTML({bundlePath: 'r1', bundleStatus: 'complete', bundleGrade: 'documented'}), / · 재현 등급 <strong>documented<\/strong>/);

// 3D: same information, re-rendered when it changes.
assert.match(live3d, /q\.assumptions, q\.bundlePath, q\.bundleStatus, q\.bundleGrade, q\.bundleWarning/);
assert.match(live3d, /재현 등급 \$\{q\.bundleGrade\}/);
// An incomplete bundle is the case a lower grade matters most (PR #431 review): both views keep the grade.
assert.match(context.bundleHTML({bundlePath: 'r1', bundleStatus: 'incomplete', bundleGrade: 'documented'}),
  /요청 묶음\(불완전\).* · 재현 등급 <strong>documented<\/strong>/);
assert.match(live3d, /MANIFEST\.tsv에 있어요\$\{grade\}`/);
assert.match(live3d, /text\(row, 'h4', '가정'\)/);
assert.match(live3d, /요청 묶음\(불완전\)/);
assert.match(live3d, /요청 묶음 경고: /);
assert.doesNotMatch(live3d, /else if \(q\.bundleWarning\)/, '3D warning is independent of the bundle path');
console.log('display follow-up web tests passed');
