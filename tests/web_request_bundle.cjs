const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..', 'labhq/web');
require(path.join(root, 'state.js'));

const office = global.LabHQState.createOfficeState();
office.apply({type: 'request.completed', request_id: 'r1', data: {
  ok: true, report: 'done', bundle_path: 'C:\\runs\\requests\\r1'
}});
assert.equal(office.S.requests.get('r1').bundlePath, 'C:\\runs\\requests\\r1');

const restored = global.LabHQState.createOfficeState();
restored.apply({type: 'snapshot', data: {agents: [], approvals: [], recent_events: [], requests: [{
  id: 'r2', text: 'failed', status: 'failed', bundle_warning: 'disk full'
}]}});
assert.equal(restored.S.requests.get('r2').bundleWarning, 'disk full');

const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
assert.match(html, /요청 묶음:/);
assert.match(html, /bundleHTML\(q\)/);
console.log('request bundle web tests passed');
