const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');

const recruiter = read('agents/core/recruiter.yaml');
assert.match(recruiter, /^name: 카피바라 인사팀$/m);
assert.match(recruiter, /^character: capybara$/m);

const assignments = fs.readdirSync(path.join(root, 'agents/core'))
  .filter(name => name.endsWith('.yaml'))
  .map(name => {
    const yaml = read(`agents/core/${name}`);
    return {
      id: yaml.match(/^id:\s*(\S+)$/m)?.[1],
      character: yaml.match(/^character:\s*(\S+)$/m)?.[1],
    };
  })
  .filter(row => row.character);
assert.equal(new Set(assignments.map(row => row.character)).size, assignments.length,
  `core staff animals must be unique: ${JSON.stringify(assignments)}`);

const state = read('labhq/web/state.js');
assert.match(state, /recruiter:'capybara'/);
assert.match(state, /editor:'beaver'/);
assert.match(state, /recruiter:'카피바라\(인사팀\)'/);

const art = read('labhq/web/ui/office-art.js');
assert.match(art, /capybara:\['#[0-9A-F]{6}','#[0-9A-F]{6}'\]/i);
assert.match(art, /kind==='capybara'/, '2D art needs a capybara-specific silhouette branch');

const demo = read('labhq/web/index.html');
assert.match(demo, /\['recruiter', '카피바라 인사팀'/);

const characters = read('labhq/web/lab3d/src/characters.js');
assert.match(characters, /id:'recruiter',name:'카피바라 인사팀',species:'카피바라'/);
assert.match(characters, /id==='recruiter'.*카피바라/s, '3D recruiter branch documents the capybara form');
assert.match(characters, /EDITOR=\{id:'editor',name:'비버 편집자',species:'비버'.*archetype:'editor'/);
const main3d = read('labhq/web/lab3d/src/main.js');
assert.match(main3d, /a\.id==='editor' \? EDITOR : null/, '3D editor must keep its own beaver archetype');

console.log('recruiter capybara identity, unique roster and artwork: OK');
