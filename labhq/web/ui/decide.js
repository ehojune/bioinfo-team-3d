const doc = () => globalThis.document;

function add(parent, tag, text = '', className = '') {
  const el = doc().createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  parent.append(el);
  return el;
}

function detailValue(value) {
  // The permission broker may send input as a serialized JSON string.
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (parsed !== null && typeof parsed === 'object') return JSON.stringify(parsed, null, 2);
    } catch {}
    return value;
  }
  return JSON.stringify(value, null, 2) ?? String(value);
}

function detailEntry(list, key, value, options = {}) {
  add(list, 'dt', key);
  const cell = add(list, 'dd');
  const text = options.raw ? String(value) : detailValue(value);
  if (options.fold || text.length > 500 || text.split('\n').length > 8) {
    const fold = add(cell, 'details');
    fold.open = Boolean(options.open);
    add(fold, 'summary', options.summary || `전체 보기 (${text.length}자) · ${text.slice(0, 120)}…`);
    add(fold, 'pre', text);
  } else add(cell, 'pre', text);
}

function renderResearchPlan(list, canonical) {
  let plan;
  try { plan = JSON.parse(canonical); } catch { detailEntry(list, '동결 PLAN 전체', canonical, {raw: true}); return; }
  const brief = plan.brief || {}, protocol = plan.protocol || {};
  detailEntry(list, '질문', brief.question ?? '');
  detailEntry(list, '가설', {
    primary: brief.primary_hypothesis,
    alternatives: brief.null_or_alternatives || [],
    distinguishing_observations: brief.distinguishing_observations || [],
  });
  detailEntry(list, '완료 조건', brief.completion_conditions || []);
  detailEntry(list, '중단 조건', protocol.stop_conditions || []);
  detailEntry(list, '자원 상한', protocol.resource_limits || []);
  detailEntry(list, 'data boundary', protocol.data_boundaries || []);
  detailEntry(list, 'pack 값', plan.pack_values || {});
  detailEntry(list, 'protocol', protocol, {fold: true, open: true, summary: 'protocol 전체'});
  detailEntry(list, '동결 PLAN 전체', canonical, {
    raw: true, fold: true, summary: `hash 입력 canonical JSON · ${canonical.length}자`,
  });
}

function renderDetail(container, kind, detail) {
  const preferred = kind === 'tool_permission' ? ['tool_name', 'input'] :
    kind === 'hpc_submit' ? ['queue', 'script_path', 'script_preview', 'cores', 'mem', 'walltime', 'resources'] : [];
  const entries = detail !== null && typeof detail === 'object' && !Array.isArray(detail) ?
    [...preferred.filter(key => Object.hasOwn(detail, key)), ...Object.keys(detail).filter(key => !preferred.includes(key))]
      .map(key => [key, detailValue(detail[key])]) : detail == null ? [] : [['detail', detailValue(detail)]];
  const signature = JSON.stringify(entries);
  // Live renders must preserve the PI's expanded values while the detail is unchanged.
  if (container._detailSignature === signature) return;
  container._detailSignature = signature;
  container.replaceChildren();
  container.hidden = entries.length === 0;
  if (!entries.length) return;
  const list = add(container, 'dl', '', 'approval-detail');
  for (const [key, value] of entries) {
    if (kind === 'research_plan' && key === 'plan_canonical') renderResearchPlan(list, detail.plan_canonical);
    else detailEntry(list, key, value, {raw: true});
  }
}

function createCard(item, options) {
  const row = doc().createElement(options.tagName || 'li');
  row.className = item.type === 'suggestion' ? 'ap sug' : 'ap';
  row.dataset.decisionKey = item.key;
  if (item.type === 'approval') row.dataset.id = item.value.id;
  else row.dataset.sug = item.value.id;
  if (options.avatarHTML) {
    const avatar = add(row, 'span', '', 'who');
    avatar.innerHTML = options.avatarHTML(item.value, item.type);
  }
  const body = add(row, 'div');
  const kind = add(body, 'div', '', 'kind');
  const title = add(kind, 'strong');
  const who = add(kind, 'span');
  const summary = add(body, 'p', '', 'sum');
  const detail = add(body, 'div', '', 'why');
  const timing = add(body, 'p', '', 'decision-meta');
  const note = add(body, 'textarea', '', 'ans');
  note.rows = item.value.kind === 'clarify' ? 3 : 2;
  const actions = add(body, 'div', '', 'acts');
  const approve = add(actions, 'button', '', 'btn go');
  approve.type = 'button';
  const deny = add(actions, 'button', '', 'btn');
  deny.type = 'button';
  row._decisionParts = { title, who, summary, detail, timing, note, approve, deny };
  return row;
}

function descendants(plan, root) {
  const found = new Set();
  let changed = true;
  while (changed) {
    changed = false;
    for (const step of plan || []) {
      if (!found.has(step.id) && (step.depends_on || []).some(id => id === root || found.has(id))) {
        found.add(step.id); changed = true;
      }
    }
  }
  return found.size;
}

export function blockedStepCount(approval, requests, taskStep) {
  const request = requests?.get ? requests.get(approval.request_id) : null;
  if (!request) return 0;
  const match = /^Step\s+(\S+)\s+needs\b/.exec(approval.summary || '');
  const sid = approval.detail?.step_id || (approval.task_id && taskStep?.get?.(approval.task_id)) || match?.[1];
  return sid ? descendants(request.plan, sid) : (approval.kind === 'clarify' ? request.plan.length : 0);
}

function timingText(approval, options) {
  const now = options.now ? options.now() : Date.now() / 1000;
  const waited = Math.max(0, now - Number(approval.created_at || now));
  const wait = waited < 60 ? `${Math.floor(waited)}초 대기` : `${Math.floor(waited / 60)}분 대기`;
  const left = Math.max(0, Number(approval.timeout_s || 0) - waited);
  const countdown = approval.timeout_s ? ` · ${left ? `${Math.ceil(left / 60)}분 남음` : '시간 초과'}` : '';
  const blocked = blockedStepCount(approval, options.requests, options.taskStep);
  return `${wait}${countdown}${blocked ? ` · 하류 ${blocked}단계 멈춤` : ''}`;
}

function updateCard(row, item, options) {
  const value = item.value, p = row._decisionParts;
  row.dataset.kind = value.kind || item.type;
  const labels = options.kindLabels || {};
  p.title.textContent = item.type === 'suggestion' ? '파견직 채용 제안' : (labels[value.kind] || value.kind || '결정');
  p.who.textContent = options.nick ? options.nick(value.agent_id) || 'CSO' : value.agent_id || 'CSO';
  p.summary.textContent = item.type === 'suggestion' ? value.repo || value.paper || value.id : value.summary || value.id;
  renderDetail(p.detail, value.kind, item.type === 'suggestion' ? value.reason : value.detail);
  p.timing.textContent = item.type === 'approval' ? timingText(value, options) : '';
  p.note.placeholder = value.kind === 'clarify' ? '답을 적어 주세요. 거절하면 요청을 멈춥니다.' : '메모(선택)';
  p.approve.textContent = item.type === 'suggestion' ? '채용하기' : value.kind === 'clarify' ? '답하고 진행' : '승인';
  p.deny.textContent = item.type === 'suggestion' ? '나중에' : '거절';
  p.approve.dataset.act = item.type === 'suggestion' ? 'hire' : 'approve';
  p.deny.dataset.act = item.type === 'suggestion' ? 'later' : 'deny';
  const disabled = options.disabled ? options.disabled(value, item.type) : false;
  p.approve.disabled = disabled; p.deny.disabled = disabled;
  if (options.onDecision) {
    p.approve.onclick = () => options.onDecision(value, true, p.note.value.trim(), item.type);
    p.deny.onclick = () => options.onDecision(value, false, p.note.value.trim(), item.type);
  }
}

export function syncDecisionCards(container, approvals, suggestions = [], options = {}) {
  const items = [
    ...Array.from(approvals || []).map(value => ({ type: 'approval', key: `approval:${value.id}`, value })),
    ...Array.from(suggestions || []).map(value => ({ type: 'suggestion', key: `suggestion:${value.id}`, value })),
  ].sort((a, b) => Number(b.value.created_at || 0) - Number(a.value.created_at || 0));
  let list = container._decisionList;
  if (!list) { list = doc().createElement(options.listTag || 'ul'); list.className = 'list'; container._decisionList = list; }
  const existing = new Map(Array.from(list.children || []).map(row => [row.dataset.decisionKey, row]));
  const rows = items.map(item => {
    const row = existing.get(item.key) || createCard(item, options);
    updateCard(row, item, options);
    return row;
  });
  if (!rows.length) {
    const empty = add(doc().createElement('div'), 'p', options.emptyText || '지금은 결정할 일이 없어요.', 'empty-note');
    container.replaceChildren(empty);
  } else {
    list.replaceChildren(...rows);
    container.replaceChildren(list);
  }
  return rows;
}

export function syncDecisionHistory(container, history, options = {}) {
  const list = doc().createElement('ol'); list.className = 'decision-history';
  for (const item of history || []) {
    const approval = item.approval || {};
    const row = add(list, 'li');
    const outcome = item.state === 'timed_out' ? '시간 초과' : item.state === 'expired' ? '만료' : item.approved ? '승인' : '거절';
    add(row, 'strong', `${outcome} · ${(options.kindLabels || {})[approval.kind] || approval.kind || '결정'}`);
    add(row, 'span', approval.summary || approval.id || '');
    if (item.note) add(row, 'small', item.note);
  }
  if (!(history || []).length) add(list, 'li', '아직 결정 이력이 없어요.', 'empty-note');
  container.replaceChildren(list);
}
