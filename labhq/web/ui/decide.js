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
  detailEntry(list, 'topics', plan.topics || []);
  detailEntry(list, 'pack 적용 판정', plan.pack_applicability || {});
  detailEntry(list, '경고', plan.warnings || []);
  detailEntry(list, 'pack 값', plan.pack_values || {});
  detailEntry(list, 'protocol', protocol, {fold: true, open: true, summary: 'protocol 전체'});
  detailEntry(list, '동결 PLAN 전체', canonical, {
    raw: true, fold: true, summary: `hash 입력 canonical JSON · ${canonical.length}자`,
  });
}

const LETTERS = 'abcd';
// The card adds the letter; an option written as "a) 승인" would read "a) a) 승인" (#331).
const optionText = o => o.trim().replace(/^\(?[a-dA-D]\)\s*(?=\S)/, '');
// Structured CSO questions (#36): options become buttons; the composed answer is the approval note (#34).
function clarifyQuestions(approval) {
  const list = approval?.kind === 'clarify' && Array.isArray(approval.detail?.questions) ? approval.detail.questions : [];
  return list.filter(q => q && typeof q.question === 'string' && q.question.trim()).map(q => {
    const options = Array.isArray(q.options) ? q.options.filter(o => typeof o === 'string' && o.trim()).slice(0, 4).map(optionText) : [];
    return { question: q.question.trim(), options: options.length >= 2 ? options : [],
      allow_free_text: options.length < 2 || q.allow_free_text !== false, depth: [30, 60, 90].includes(q.depth) ? q.depth : null };
  });
}

function renderQuestions(container, approval) {
  const questions = clarifyQuestions(approval);
  const signature = JSON.stringify(questions);
  // Live renders must keep the PI's half-finished choices.
  if (container._questionSignature === signature) return;
  container._questionSignature = signature;
  container.replaceChildren();
  container.hidden = questions.length === 0;
  container._questions = questions;
  container._answers = questions.map((question, index) => {
    const answer = { choice: null, free: null };
    const block = add(container, 'fieldset', '', 'clarify-q');
    add(block, 'legend', `${index + 1}. ${question.question}`);
    if (question.depth) add(block, 'small', `깊이 약 ${question.depth}분`, 'clarify-depth');
    if (question.options.length) {
      const group = add(block, 'div', '', 'clarify-options');
      const buttons = question.options.map((option, choice) => {
        const button = add(group, 'button', `${LETTERS[choice]}) ${option}`, 'btn opt');
        button.type = 'button'; button.ariaPressed = 'false';
        button.onclick = () => {
          answer.choice = answer.choice === choice ? null : choice;
          buttons.forEach((other, i) => { other.ariaPressed = String(answer.choice === i); other.className = answer.choice === i ? 'btn opt on' : 'btn opt'; });
        };
        return button;
      });
    }
    if (question.allow_free_text) {
      answer.free = add(block, 'input', '', 'clarify-free');
      answer.free.type = 'text';
      answer.free.placeholder = question.options.length ? '직접 답하기(선택)' : '답을 적어 주세요';
    }
    return answer;
  });
}

function renderAssumptions(container, approval) {
  const assumptions = approval?.kind === 'clarify' && Array.isArray(approval.detail?.assumptions)
    ? approval.detail.assumptions.filter(value => typeof value === 'string' && value.trim()).slice(0, 8) : [];
  const signature = JSON.stringify(assumptions);
  if (container._assumptionSignature === signature) return;
  container._assumptionSignature = signature;
  container.replaceChildren();
  container.hidden = assumptions.length === 0;
  if (!assumptions.length) return;
  add(container, 'strong', '가정');
  const list = add(container, 'ul');
  for (const value of assumptions) add(list, 'li', value.trim());
  add(container, 'p', '바꾸려면 실행 중 메모로 알려 주세요.', 'empty-note');
}

// The note to send: for structured questions every question needs a choice or text, otherwise ''.
export function decisionNote(row, approved = true) {
  const parts = row?._decisionParts;
  if (!parts) return '';
  const note = String(parts.note.value || '').trim();
  const questions = parts.questions?._questions || [];
  if (!approved || !questions.length) return note;
  const lines = [];
  for (const [index, question] of questions.entries()) {
    const answer = parts.questions._answers[index], picked = [];
    if (answer.choice !== null) picked.push(`${LETTERS[answer.choice]}) ${question.options[answer.choice]}`);
    const free = answer.free ? String(answer.free.value || '').trim() : '';
    if (free) picked.push(free);
    if (!picked.length) return '';
    lines.push(`Q${index + 1}. ${picked.join(' — ')}`);
  }
  if (note) lines.push(`메모: ${note}`);
  return lines.join('\n');
}

// CP2 evidence review (#90): the decision is a structured choice; the note is only a memo.
const EVIDENCE_LABELS = { refused_rows: '계약에 맞지 않아 뺀 근거',
  refused_evidence: '거부된 evidence (승인 대상 아님)', unsupported_claims: '근거를 잃은 claim',
  results: '단계별 claim·evidence 원장' };
export function decisionChoice(approval, act) {
  if (approval?.kind !== 'research_evidence') return null;
  return { approve: 'approve', revise: 'revise', deny: 'deny' }[act] || null;
}

function renderDetail(container, kind, detail) {
  const preferred = kind === 'tool_permission' ? ['tool_name', 'input'] :
    kind === 'hpc_submit' ? ['queue', 'script_path', 'script_preview', 'cores', 'mem', 'walltime', 'resources'] :
    kind === 'research_evidence' ? ['refused_rows', 'refused_evidence', 'unsupported_claims', 'plan_sha256', 'results'] : [];
  const shown = kind === 'clarify' && Array.isArray(detail?.questions) ? ['questions', 'assumptions'] :
    kind === 'research_evidence' ? ['choices'] : [];
  const entries = detail !== null && typeof detail === 'object' && !Array.isArray(detail) ?
    [...preferred.filter(key => Object.hasOwn(detail, key)), ...Object.keys(detail).filter(key => !preferred.includes(key) && !shown.includes(key))]
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
    else if (kind === 'research_evidence' && EVIDENCE_LABELS[key]) detailEntry(list, EVIDENCE_LABELS[key], value, {raw: true});
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
  const questions = add(body, 'div', '', 'clarify');
  questions.hidden = true;
  const assumptions = add(body, 'div', '', 'plan-assumptions');
  assumptions.hidden = true;
  const timing = add(body, 'p', '', 'decision-meta');
  const note = add(body, 'textarea', '', 'ans');
  note.rows = item.value.kind === 'clarify' ? 3 : 2;
  const actions = add(body, 'div', '', 'acts');
  const approve = add(actions, 'button', '', 'btn go');
  approve.type = 'button';
  const revise = add(actions, 'button', '', 'btn');  // CP2 evidence review only
  revise.type = 'button'; revise.hidden = true;
  const deny = add(actions, 'button', '', 'btn');
  deny.type = 'button';
  row._decisionParts = { title, who, summary, detail, questions, assumptions, timing, note, approve, revise, deny };
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
  renderQuestions(p.questions, item.type === 'approval' ? value : null);
  renderAssumptions(p.assumptions, item.type === 'approval' ? value : null);
  p.timing.textContent = item.type === 'approval' ? timingText(value, options) : '';
  p.note.placeholder = value.kind !== 'clarify' ? '메모(선택)' : p.questions._questions?.length
    ? '덧붙일 말(선택). 거절하면 요청을 멈춥니다.' : '답을 적어 주세요. 거절하면 요청을 멈춥니다.';
  const evidence = item.type === 'approval' && value.kind === 'research_evidence';
  const scope = item.type === 'approval' && value.kind === 'scope';  // out-of-scope request: run it or stop (#36)
  p.approve.textContent = item.type === 'suggestion' ? '채용하기' : evidence ? '증거 승인' : value.kind === 'clarify' ? '답하고 진행' : scope ? '진행' : '승인';
  p.deny.textContent = item.type === 'suggestion' ? '나중에' : evidence ? '거부' : scope ? '중단' : '거절';
  p.revise.textContent = '수정 요청'; p.revise.hidden = !evidence;
  p.approve.dataset.act = item.type === 'suggestion' ? 'hire' : 'approve';
  p.revise.dataset.act = 'revise';
  p.deny.dataset.act = item.type === 'suggestion' ? 'later' : 'deny';
  const disabled = options.disabled ? options.disabled(value, item.type) : false;
  p.approve.disabled = disabled; p.revise.disabled = disabled; p.deny.disabled = disabled;
  if (options.onDecision) {
    p.approve.onclick = () => options.onDecision(value, true, decisionNote(row, true), item.type, decisionChoice(value, 'approve'));
    p.revise.onclick = () => options.onDecision(value, false, decisionNote(row, false), item.type, decisionChoice(value, 'revise'));
    p.deny.onclick = () => options.onDecision(value, false, decisionNote(row, false), item.type, decisionChoice(value, 'deny'));
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
    placeRows(list, rows);
    if (container.children.length !== 1 || container.children[0] !== list) container.replaceChildren(list);
  }
  return rows;
}

// Re-inserting a row detaches it, and a detached textarea loses focus mid-answer (#57). Only finished rows
// leave and only new or reordered rows move; a row already in place is never touched.
function placeRows(list, rows) {
  const keep = new Set(rows);
  for (const row of Array.from(list.children)) if (!keep.has(row)) list.removeChild(row);
  rows.forEach((row, index) => {
    const current = list.children[index];
    if (current === row) return;
    if (current) list.insertBefore(row, current); else list.append(row);
  });
}

export function syncDecisionHistory(container, history, options = {}) {
  const items = Array.from(history || []);
  const pageSize = Math.max(1, Number(options.pageSize) || 10);
  if (!Number.isFinite(container._historyShown) || container._historyShown < pageSize) container._historyShown = pageSize;
  const visible = items.slice(0, container._historyShown);
  const root = doc().createElement('div'); root.className = 'decision-history';
  const groups = new Map();
  for (const item of visible) {
    const approval = item.approval || {}, requestId = approval.request_id || item.request_id || 'other';
    if (!groups.has(requestId)) groups.set(requestId, []);
    groups.get(requestId).push(item);
  }
  for (const [requestId, values] of groups) {
    const group = doc().createElement('section'); group.className = 'decision-history-group'; group.dataset.requestId = requestId;
    const request = options.requests?.get?.(requestId);
    const label = request?.text ? String(request.text).replace(/\s+/g, ' ').slice(0, 44) : requestId === 'other' ? '기타 요청' : `요청 ${requestId}`;
    add(group, 'h3', label);
    const list = add(group, 'ol');
    for (const item of values) {
      const approval = item.approval || {};
      const row = add(list, 'li', '', 'decision-history-item');
      const outcome = item.state === 'timed_out' ? '시간 초과' : item.state === 'expired' ? '만료' : item.approved ? '승인' :
        item.choice === 'revise' ? '수정 요청' : '거절';
      add(row, 'strong', `${outcome} · ${(options.kindLabels || {})[approval.kind] || approval.kind || '결정'}`);
      add(row, 'span', approval.summary || approval.id || '');
      if (item.note) add(row, 'small', item.note);
    }
    root.append(group);
  }
  if (!items.length) add(root, 'p', '아직 결정 이력이 없어요.', 'empty-note');
  const children = [root];
  if (visible.length < items.length) {
    const more = doc().createElement('button'); more.type = 'button'; more.className = 'btn decision-history-more';
    more.textContent = `더 보기 (${items.length - visible.length}건)`;
    more.onclick = () => { container._historyShown += pageSize; syncDecisionHistory(container, items, options); };
    children.push(more);
  }
  container.replaceChildren(...children);
}
