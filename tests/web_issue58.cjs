const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', 'labhq', 'web');
const office = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const live3d = fs.readFileSync(path.join(root, 'lab3d', 'src', 'live.js'), 'utf8');

for (const source of [office, live3d]) {
  assert.match(source, /audit-bundle/);
  assert.match(source, /감사 번들/);
  assert.match(source, /Authorization/);
}

console.log('web issue58 tests passed');
