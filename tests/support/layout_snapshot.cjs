// Layout snapshot of the 2D office page: boxes, visibility and key computed colors of the main components.
// Not a pixel comparison. Positions of inner parts are stored relative to their section (anchor), so one
// section growing does not shift every record below it.
'use strict';

const DEFAULT_TOLERANCE = {px: 4, frac: 0.01, loosePx: 12, looseFrac: 0.03, colorChannel: 2, docHeightFrac: 0.02};

// group: record group name; sel: CSS selector; each: one record per match (keyed) or the first match;
// anchor: section the position is relative to; views: which views record it ('default' is the decisions tab, 'staff' the expanded staff strip); loose: wider tolerance
// (native form controls differ a little between operating systems); limit: max records; text: record the label;
// paintOnly: record visibility and colors but no box.
const SPEC = [
  {group: 'top', sel: '.top', views: '*'},
  {group: 'office', sel: '#office', views: ['default']},
  {group: 'wall-board', sel: '#board', anchor: 'office', views: ['default']},
  {group: 'rack', sel: '#rack', anchor: 'office', views: ['default']},
  {group: 'door', sel: '#door', anchor: 'office', views: ['default']},
  {group: 'zone', sel: '#zones .zone', each: 'zone', anchor: 'office', views: ['default']},
  {group: 'seat', sel: '#zones [data-agent]', each: 'agent', anchor: 'office', views: ['default'], status: 'data-status'},
  {group: 'character', sel: '#zones [data-agent] .office-character', each: 'parent-agent', anchor: 'office', views: ['default']},
  {group: 'seat-status', sel: '#zones [data-agent] .office-status', each: 'parent-agent', anchor: 'office', views: ['default'], text: true},
  {group: 'seat-dot', sel: '#zones [data-agent] .status-dot', each: 'parent-agent', anchor: 'office', views: ['default'], paintOnly: true},
  {group: 'cc', sel: '#command-center', views: '*'},
  {group: 'tab', sel: '#cc-tabs .ui-tab', each: 'tabTarget', anchor: 'cc', views: '*', status: 'aria-selected', text: true},
  {group: 'tab-count', sel: '#decision-count', anchor: 'cc', views: '*', text: true},
  {group: 'panel', sel: '#command-center .panel', each: 'tab', anchor: 'cc', views: '*'},
  {group: 'decision', sel: '#approvals .decision-card', each: 'decisionKey', anchor: 'cc', views: ['default']},
  {group: 'decision-status', sel: '#approvals .decision-card .decision-status', each: 'index', anchor: 'cc', views: ['default'], text: true},
  {group: 'decision-primary', sel: '#approvals .decision-card .decision-primary', each: 'index', anchor: 'cc', views: ['default'], text: true},
  {group: 'review', sel: '#review-cards .decision-card', each: 'reviewRequest', anchor: 'cc', views: ['default']},
  {group: 'review-primary', sel: '#review-cards .decision-card .decision-primary', each: 'index', anchor: 'cc', views: ['default'], text: true},
  {group: 'task-board', sel: '#request', anchor: 'cc', views: ['tasks']},
  {group: 'task-choice', sel: '#request .request-choice', each: 'index', anchor: 'cc', views: ['tasks'], limit: 6},
  {group: 'task-column', sel: '#request .kanban-column', each: 'index', anchor: 'cc', views: ['tasks']},
  {group: 'task-card', sel: '#request .task-card', each: 'index', anchor: 'cc', views: ['tasks'], limit: 8},
  {group: 'messenger', sel: '#feed', anchor: 'cc', views: ['feed']},
  {group: 'message', sel: '#feed li', each: 'index', anchor: 'cc', views: ['feed'], limit: 8},
  {group: 'staff-wrap', sel: '.staff-wrap', views: ['default', 'staff']},
  {group: 'staff-toggle', sel: '#staff-toggle', anchor: 'staff', views: ['default', 'staff'], text: true, status: 'aria-expanded'},
  {group: 'staff-strip', sel: '#staff-strip', anchor: 'staff', views: ['default', 'staff']},
  {group: 'staff-card', sel: '#staff-strip .staff-card', each: 'agent', anchor: 'staff', views: ['staff'], status: 'staff-status'},
  {group: 'staff-chip', sel: '#staff-strip .staff-card .staff-status', each: 'parent-agent', anchor: 'staff', views: ['staff'], text: true},
  {group: 'staff-dot', sel: '#staff-strip .staff-card .status-dot', each: 'parent-agent', anchor: 'staff', views: ['staff'], paintOnly: true},
  {group: 'composer', sel: '.composer', views: ['default'], loose: true},
];
const ANCHORS = {office: '#office', cc: '#command-center', staff: '.staff-wrap'};

// Expression evaluated in the page; `view` is 'default', 'tasks' or 'feed'.
function snapshotSource(view) {
  return `((SPEC, ANCHORS, view) => {
    window.scrollTo(0, 0);
    const sx = window.scrollX, sy = window.scrollY;
    const box = e => { const b = e.getBoundingClientRect(); return {x: b.left + sx, y: b.top + sy, w: b.width, h: b.height}; };
    const shown = e => {
      for (let p = e; p && p.nodeType === 1; p = p.parentElement) {
        if (p.hidden) return false;
        const s = getComputedStyle(p);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
      }
      const b = e.getBoundingClientRect();
      return b.width > 0 && b.height > 0;
    };
    // #rrggbb, or #rrggbbaa when translucent; null for fully transparent.
    const hex = css => {
      const n = (css.match(/[0-9.]+/g) || []).map(Number);
      if (n.length < 3) return css;
      const a = n.length > 3 ? n[3] : 1;
      if (a === 0) return null;
      const h = v => Math.round(v).toString(16).padStart(2, '0');
      return '#' + h(n[0]) + h(n[1]) + h(n[2]) + (a < 1 ? h(a * 255) : '');
    };
    const paint = e => {
      const s = getComputedStyle(e), out = {color: hex(s.color)}, bg = hex(s.backgroundColor);
      if (bg) out.background = bg;
      if (parseFloat(s.borderTopWidth) > 0 && hex(s.borderTopColor)) out.border = hex(s.borderTopColor);
      return out;
    };
    const anchors = {};
    for (const [name, sel] of Object.entries(ANCHORS)) { const a = document.querySelector(sel); if (a) anchors[name] = box(a); }
    const keyOf = (e, how, index) => {
      if (how === 'index') return String(index);
      if (how === 'parent-agent') return e.closest('[data-agent]').dataset.agent;
      return e.dataset[how] || String(index);
    };
    const items = {};
    for (const spec of SPEC) {
      if (spec.views !== '*' && !spec.views.includes(view)) continue;
      let list = [...document.querySelectorAll(spec.sel)];
      if (!spec.each) list = list.slice(0, 1);
      if (spec.limit) list = list.slice(0, spec.limit);
      items[spec.group] = list.map((e, i) => {
        const rec = {key: spec.each ? keyOf(e, spec.each, i) : spec.group};
        rec.shown = shown(e);
        if (spec.loose) rec.loose = true;
        if (spec.status) {
          const raw = spec.status === 'staff-status' ? [...e.classList].find(c => c.startsWith('staff-') && c !== 'staff-card') : e.getAttribute(spec.status);
          if (raw) rec.status = raw;
        }
        if (rec.shown) {
          const b = box(e), a = spec.anchor && anchors[spec.anchor];
          if (!spec.paintOnly) rec.box = {x: Math.round(b.x - (a ? a.x : 0)), y: Math.round(b.y - (a ? a.y : 0)), w: Math.round(b.w), h: Math.round(b.h)};
          rec.paint = paint(e);
        }
        const text = (e.textContent || '').trim();
        if (spec.text) rec.text = text;
        return rec;
      });
    }
    const de = document.documentElement;
    return {viewport: {w: innerWidth, h: innerHeight}, doc: {w: de.scrollWidth, h: de.scrollHeight}, items};
  })(${JSON.stringify(SPEC)}, ${JSON.stringify(ANCHORS)}, ${JSON.stringify(view)})`;
}

// [r, g, b, alpha 0..1] of a stored color. The snapshot writes #rrggbb (or #rrggbbaa, alpha as a byte); each pair of
// hex digits is one channel. Decimal rgb()/rgba() text still reads as before. null when it is neither.
function colorChannels(css) {
  const text = String(css).trim();
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})?$/i.exec(text);
  if (hex) return [1, 2, 3].map(i => parseInt(hex[i], 16)).concat(hex[4] ? parseInt(hex[4], 16) / 255 : 1);
  const n = (text.match(/[\d.]+/g) || []).map(Number);
  return n.length >= 3 ? [n[0], n[1], n[2], n.length > 3 ? n[3] : 1] : null;
}
function sameColor(a, b, tolerance) {
  if (a === b) return true;
  const x = colorChannels(a), y = colorChannels(b);
  if (!x || !y) return false;
  return x.slice(0, 3).every((v, i) => Math.abs(v - y[i]) <= tolerance.colorChannel) && Math.abs(x[3] - y[3]) <= 0.02;
}

// Differences between a baseline view and a new one, as readable lines. Empty means the layout matches.
function diffView(label, base, now, tolerance = DEFAULT_TOLERANCE) {
  const out = [];
  const near = (a, b, ref, loose) => {
    const px = loose ? tolerance.loosePx : tolerance.px, frac = loose ? tolerance.looseFrac : tolerance.frac;
    return Math.abs(a - b) <= Math.max(px, frac * ref);
  };
  const dims = {x: 'w', w: 'w', y: 'h', h: 'h'};
  const vp = now.viewport;
  if (base.viewport.w !== vp.w || base.viewport.h !== vp.h) out.push(`${label}: viewport ${base.viewport.w}x${base.viewport.h} -> ${vp.w}x${vp.h}`);
  if (now.doc.w > vp.w) out.push(`${label}: page is ${now.doc.w}px wide in a ${vp.w}px viewport (horizontal overflow)`);
  if (Math.abs(base.doc.h - now.doc.h) > Math.max(tolerance.px, tolerance.docHeightFrac * base.doc.h)) out.push(`${label}: page height ${base.doc.h} -> ${now.doc.h}`);
  for (const group of new Set([...Object.keys(base.items), ...Object.keys(now.items)])) {
    const was = new Map((base.items[group] || []).map(r => [r.key, r])), is = new Map((now.items[group] || []).map(r => [r.key, r]));
    for (const key of was.keys()) if (!is.has(key)) out.push(`${label}: ${group}[${key}] is gone`);
    for (const key of is.keys()) if (!was.has(key)) out.push(`${label}: ${group}[${key}] is new`);
    for (const [key, a] of was) {
      const b = is.get(key);
      if (!b) continue;
      const at = `${label}: ${group}[${key}]`;
      if (a.shown !== b.shown) { out.push(`${at} shown ${a.shown} -> ${b.shown}`); continue; }
      if (a.status !== b.status) out.push(`${at} status ${a.status} -> ${b.status}`);
      if (a.text !== b.text) out.push(`${at} text "${a.text}" -> "${b.text}"`);
      if (!a.shown) continue;
      if (Boolean(a.box) !== Boolean(b.box)) out.push(`${at} box ${a.box ? 'present' : 'absent'} -> ${b.box ? 'present' : 'absent'}`);
      else if (a.box) {
        for (const k of ['x', 'y', 'w', 'h']) {
          const ref = vp[dims[k]];
          if (!near(a.box[k], b.box[k], ref, a.loose)) out.push(`${at} ${k} ${a.box[k]} -> ${b.box[k]}`);
        }
      }
      for (const k of new Set([...Object.keys(a.paint), ...Object.keys(b.paint)])) {
        if (!sameColor(a.paint[k], b.paint[k], tolerance)) out.push(`${at} paint.${k} ${a.paint[k]} -> ${b.paint[k]}`);
      }
    }
  }
  return out;
}

// One record per line keeps the committed baseline readable in review.
function formatBaseline(data) {
  const lines = [];
  const rec = r => JSON.stringify(r);
  lines.push('{');
  lines.push(`"version": ${data.version},`);
  lines.push(`"note": ${JSON.stringify(data.note)},`);
  lines.push(`"tolerance": ${JSON.stringify(data.tolerance)},`);
  lines.push('"views": {');
  const names = Object.keys(data.views);
  names.forEach((name, i) => {
    const v = data.views[name];
    lines.push(`${JSON.stringify(name)}: {"viewport": ${JSON.stringify(v.viewport)}, "doc": ${JSON.stringify(v.doc)}, "items": {`);
    const groups = Object.keys(v.items);
    groups.forEach((group, j) => {
      lines.push(`  ${JSON.stringify(group)}: [`);
      v.items[group].forEach((r, k) => lines.push(`    ${rec(r)}${k < v.items[group].length - 1 ? ',' : ''}`));
      lines.push(`  ]${j < groups.length - 1 ? ',' : ''}`);
    });
    lines.push(`}}${i < names.length - 1 ? ',' : ''}`);
  });
  lines.push('}', '}');
  return lines.join('\n') + '\n';
}

module.exports = {DEFAULT_TOLERANCE, SPEC, snapshotSource, diffView, formatBaseline, sameColor, colorChannels};
