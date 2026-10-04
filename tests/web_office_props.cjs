const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// #57 ⑧: each office prop is a tab entrance, and waiting decisions turn the board into the way to 결정.
const html = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'labhq/web/index.html'), 'utf8');
const links = [...html.matchAll(/data-tab-link="([a-z]+)"/g)].map(m => m[1]);
assert.deepEqual(links, ['tasks', 'jobs', 'feed'], 'board, HPC rack and door each open a tab');
for (const tab of links) assert.match(html, new RegExp(`data-tab="${tab}"`), `${tab} is a real panel`);
assert.match(html, /board\.dataset\.tabLink = total \? 'decisions' : 'tasks'/);
assert.match(html, /board\.classList\.toggle\('alert', total > 0\)/);
assert.match(html, /\.board\.alert\{animation:boardBlink/, 'the board blinks only when motion is allowed');
assert.match(html, /addEventListener\('keydown', openPropTab\)/, 'props open from the keyboard too');
console.log('office prop tab entrances: OK');
