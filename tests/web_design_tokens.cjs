const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const web = path.resolve(__dirname, '../labhq/web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const tokens = fs.readFileSync(path.join(web, 'ui/tokens.css'), 'utf8');
const components = fs.readFileSync(path.join(web, 'ui/components.css'), 'utf8');
const palette = Object.fromEntries([...tokens.matchAll(/--ds-([\w-]+):\s*light-dark\((#[\da-f]{6}),\s*(#[\da-f]{6})\)/gi)]
  .map(([, name, light, dark]) => [name, [light, dark]]));
function luminance(hex) {
  const rgb = hex.slice(1).match(/../g).map(x => parseInt(x, 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
}
function contrast(a, b) {
  const [lo, hi] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  return (hi + .05) / (lo + .05);
}
test('2D loads the token and component files after legacy rules; every ds reference resolves', () => {
  const styles = [...html.matchAll(/<link[^>]+href="([^"]+\.css)"/g)].map(m => m[1]);
  assert.deepEqual(styles.slice(-3), ['ui/tokens.css', 'ui/components.css', 'ui/office.css']);
  assert.ok(html.indexOf('href="ui/tokens.css"') > html.indexOf('</style>'));
  const declared = new Set([...tokens.matchAll(/(--ds-[\w-]+):/g)].map(m => m[1]));
  for (const [, name] of (components + html + tokens + fs.readFileSync(path.join(web, 'ui/office.css'), 'utf8')).matchAll(/var\((--ds-[\w-]+)/g)) {
    assert.ok(declared.has(name), `undefined token: ${name}`);
  }
  for (let i = 1; i <= 6; i++) assert.ok(declared.has(`--ds-fs-${i}`));
  assert.match(tokens, /--ds-h-touch:\s*48px/);
  assert.match(tokens, /:root\[data-theme="light"\]\{ color-scheme: light; \}/);
  assert.match(tokens, /:root\[data-theme="dark"\]\{ color-scheme: dark; \}/);
  assert.match(tokens, /prefers-color-scheme: dark/);
  assert.equal((html.match(/var\(--brand-font\)/g) || []).length, 1);
  assert.doesNotMatch(html, /var\(--hand\)/);
  assert.match(components, /\.btn\.primary, \.btn\.go/);
  assert.match(components, /\.ui-tab__count/);
  assert.match(components, /\.chip\[class\*="st-"\]\)::before/);
  assert.doesNotMatch(fs.readFileSync(path.join(web, 'lab3d/index.html'), 'utf8'), /ui\/(tokens|components)\.css/);
});
for (const [mode, index] of [['light', 0], ['dark', 1]]) {
  test(`${mode}: primary, secondary, status and toast text meet WCAG AA 4.5:1`, t => {
    const pairs = [];
    for (const fg of ['ink', 'ink-2', 'ink-3']) {
      for (const bg of ['paper', 'surface', 'sunk']) pairs.push([fg, bg]);
    }
    pairs.push(['on-accent', 'accent'], ['accent', 'surface'], ['accent', 'accent-soft'], ['paper', 'ink']);
    for (const state of ['work', 'hpc', 'wait', 'warn', 'err']) pairs.push([state, `${state}-bg`]);
    pairs.push(['err', 'surface']);
    let minimum = Infinity;
    for (const [fg, bg] of pairs) {
      assert.ok(palette[fg] && palette[bg], `missing pair ${fg}/${bg}`);
      const ratio = contrast(palette[fg][index], palette[bg][index]);
      minimum = Math.min(minimum, ratio);
      assert.ok(ratio >= 4.5, `${mode} ${fg}/${bg}: ${ratio.toFixed(2)}:1`);
    }
    t.diagnostic(`${pairs.length} text/background pairs; minimum ${minimum.toFixed(2)}:1`);
  });
}
