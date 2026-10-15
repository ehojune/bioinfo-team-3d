const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const read = rel => fs.readFileSync(path.join(root, rel), 'utf8');

const context = vm.createContext({console});
vm.runInContext(read('labhq/web/state.js'), context);
assert.equal(context.LabHQState.staffCharacter({id: 'solo'}), 'lion',
  'the local solo agent defaults to a lion without a character field');

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
assignments.push({id: 'solo', character: context.LabHQState.staffCharacter({id: 'solo'})});
assert.equal(new Set(assignments.map(row => row.character)).size, assignments.length,
  `core staff and solo animals must be unique: ${JSON.stringify(assignments)}`);

const art = read('labhq/web/ui/office-art.js');
assert.match(art, /lion:\['#[0-9A-F]{6}','#[0-9A-F]{6}'\]/i);
assert.match(art, /kind==='lion'/, '2D art needs a lion-specific mane branch');
assert.match(art, /solo:'checklist'/, 'the solo lion carries an end-to-end checklist');

const characters = read('labhq/web/lab3d/src/characters.js');
assert.match(characters, /export const SOLO=\{id:'solo',name:'사자 단독 처리',species:'사자'/);
assert.match(characters, /id==='solo'.*갈기/s, '3D solo branch documents the lion mane');
const main3d = read('labhq/web/lab3d/src/main.js');
assert.match(main3d, /a\.id==='solo' \? SOLO : null/, 'live 3D roster routes solo to the lion archetype');

console.log('solo lion default, unique roster and artwork: OK');
