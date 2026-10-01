// Reference pointer chips for the request composer (#36). Same kind rules as `labhq send --ref`
// (labhq/intake.py infer_reference); the gateway validates and normalizes every value.
const doc = () => globalThis.document;
const OWNER = '[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})', REPO = '[A-Za-z0-9._-]{1,100}', BRANCH = '[A-Za-z0-9._/-]{1,200}';
const SHORTHAND = new RegExp(`^${OWNER}/${REPO}(?:@${BRANCH})?$`);
export const KIND_LABELS = { github: 'GitHub', doi: 'DOI', pmid: 'PMID', url: 'URL', path: '경로' };

export function inferReference(input) {
  const text = String(input ?? '').trim();
  const prefixed = /^(github|doi|pmid|url|path):(.*)$/i.exec(text);
  if (prefixed && prefixed[2].trim()) return { kind: prefixed[1].toLowerCase(), value: prefixed[2].trim() };
  if (/^(?:https?:\/\/(?:dx\.)?doi\.org\/)?10\.\d{4,9}\/\S+$/i.test(text)) return { kind: 'doi', value: text };
  if (/^\d{1,9}$/.test(text)) return { kind: 'pmid', value: text };
  if (/^https?:\/\/(?:www\.)?github\.com\//i.test(text)) return { kind: 'github', value: text };
  if (/^https?:\/\//i.test(text)) return { kind: 'url', value: text };
  if (/^[/~]/.test(text) || /^[A-Za-z]:[\\/]/.test(text)) return { kind: 'path', value: text };
  if (SHORTHAND.test(text)) return { kind: 'github', value: text };
  return null;
}

function add(parent, tag, text = '', className = '') {
  const el = doc().createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  parent.append(el);
  return el;
}

// state: {items: [{kind, value}], useDefaults: bool}; defaults: pi_profile.references from the snapshot.
export function syncReferenceChips(container, state, defaults = [], options = {}) {
  container.replaceChildren();
  (state.items || []).forEach((ref, index) => {
    const chip = add(container, 'span', '', 'chip ref-chip');
    add(chip, 'b', KIND_LABELS[ref.kind] || ref.kind);
    add(chip, 'span', ` ${ref.value} `);
    const remove = add(chip, 'button', '×', 'ref-remove');
    remove.type = 'button'; remove.dataset.refRemove = String(index); remove.ariaLabel = `${ref.value} 빼기`;
    remove.onclick = () => options.onRemove?.(index);
  });
  if (defaults.length) {
    const toggle = add(container, 'button', `기본 참고 ${defaults.length}건 ${state.useDefaults ? '포함' : '빼기'}`, 'chip ref-default');
    toggle.type = 'button'; toggle.dataset.refDefaults = '1'; toggle.ariaPressed = String(Boolean(state.useDefaults));
    toggle.title = defaults.map(ref => `${KIND_LABELS[ref.kind] || ref.kind} ${ref.value}${ref.note ? ` — ${ref.note}` : ''}`).join('\n');
    toggle.onclick = () => options.onToggleDefaults?.();
  }
  container.hidden = !(state.items || []).length && !defaults.length;
}
