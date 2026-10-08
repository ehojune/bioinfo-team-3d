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

const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const bullets = values => (Array.isArray(values) ? values : values == null ? [] : [values])
  .map(value => `- ${typeof value === 'string' ? value : JSON.stringify(value)}`).join('\n') || '(없음)';

// CP1 (R18): the plan reads as sections and a step list; the full protocol and hash input stay folded.
function renderResearchPlan(list, canonical) {
  let plan;
  try { plan = JSON.parse(canonical); } catch { detailEntry(list, '동결 PLAN 전체', canonical, {raw: true}); return; }
  const brief = plan.brief || {}, protocol = plan.protocol || {}, steps = Array.isArray(plan.steps) ? plan.steps : [];
  detailEntry(list, '질문', brief.question ?? '', {raw: true});
  detailEntry(list, '가설', [`주 가설: ${brief.primary_hypothesis ?? ''}`, '대안·귀무:', bullets(brief.null_or_alternatives),
    '가르는 관찰:', bullets(brief.distinguishing_observations)].join('\n'), {raw: true, open: true});
  detailEntry(list, '완료 조건', bullets(brief.completion_conditions), {raw: true});
  detailEntry(list, '단계', steps.map(step => {
    const outputs = Array.isArray(step.outputs) ? step.outputs.join(', ') : '';
    return `${step.id} · ${step.agent_id || '미배정'} · ${short(step.instruction, 120)}${outputs ? `\n    산출: ${outputs}` : ''}`;
  }).join('\n') || '(없음)', {raw: true, fold: steps.length > 0, open: true, summary: `단계 ${steps.length}개 (id · 직원 · 지시 앞부분 · 산출)`});
  detailEntry(list, '중단 조건', bullets(protocol.stop_conditions), {raw: true});
  detailEntry(list, '자원 상한', bullets(protocol.resource_limits), {raw: true});
  detailEntry(list, 'data boundary', bullets(protocol.data_boundaries), {raw: true});
  detailEntry(list, 'topics', (plan.topics || []).join(', ') || '(없음)', {raw: true});
  detailEntry(list, 'pack 적용 판정', Object.entries(plan.pack_applicability || {}).map(([pack, verdict]) =>
    `${pack}: ${verdict?.applied ? '적용' : '적용 안 함'} · ${verdict?.reason || ''}` +
    `${(verdict?.matched_topics || []).length ? ` · ${verdict.matched_topics.join(', ')}` : ''}`).join('\n') || '(없음)', {raw: true});
  if ((plan.warnings || []).length) detailEntry(list, '경고', bullets(plan.warnings), {raw: true});
  detailEntry(list, 'pack 값', plan.pack_values || {});
  detailEntry(list, 'protocol', protocol, {fold: true, summary: 'protocol 전체 (펼쳐 보기)'});
  detailEntry(list, '동결 PLAN 전체', canonical, {
    raw: true, fold: true, summary: `hash 입력 canonical JSON · ${canonical.length}자`,
  });
}

// Review "revise" card: the P1 issues the new plan must answer, readable before the raw JSON.
function renderReviewIssues(list, issues) {
  const rows = Array.isArray(issues) ? issues : [];
  detailEntry(list, `리뷰 P1 지적 (${rows.length}건)`, rows.map((issue, index) => [
    `${index + 1}. ${issue?.step_id || '단계 없음'}${issue?.claim_id ? ` · claim ${issue.claim_id}` : ''}`,
    `   문제: ${issue?.problem ?? ''}`, ...(issue?.request ? [`   고칠 점: ${issue.request}`] : []),
  ].join('\n')).join('\n\n') || '(없음)', {raw: true, fold: rows.length > 0, open: true,
    summary: `P1 ${rows.length}건 (단계 · 문제 · 고칠 점)`});
  if (rows.length) detailEntry(list, '리뷰 지적 JSON 전체', detailValue(rows), {raw: true, fold: true});
}

// CP2 (R18): one row per claim, built from the card's ledgers; the raw ledger JSON stays folded below.
const CLAIM_STATUS_KO = { supported: '지지', partially_supported: '부분 지지', contradicted: '반대 근거',
  unresolved: '미해결', proposed: '제안', withdrawn: '철회' };
const EVIDENCE_KIND_KO = { observation: '관찰', database_annotation: 'DB 주석', experimental: '실험',
  literature_claim: '문헌', inference: '추론', hypothesis: '가설' };
const EVIDENCE_STATUS_KO = { not_found: '0건', failed: '조회 실패', unavailable: '접근 불가' };
const RELATION_KO = { contradicts: '반대', context: '맥락' };
export function evidenceRows(detail) {
  const results = detail && typeof detail.results === 'object' && detail.results ? detail.results : {};
  const refused = new Set([...(detail?.refused_evidence || []).map(row => `${row.step_id}/${row.evidence_id}`),
    ...(detail?.refused_rows || []).filter(row => row.row_type === 'evidence').map(row => `${row.step_id}/${row.row_id}`)]);
  const unsupported = new Set((detail?.unsupported_claims || []).map(row => `${row.step_id}/${row.claim_id}`));
  const rows = [];
  for (const [sid, ledger] of Object.entries(results)) {
    const evidence = new Map((ledger?.evidence || []).map(row => [row.id, row]));
    for (const claim of ledger?.claims || []) {
      const links = (ledger.links || []).filter(link => link.claim_id === claim.id);
      const kinds = new Map(), marks = [];
      for (const link of links) {
        const row = evidence.get(link.evidence_id) || {};
        const label = `${RELATION_KO[link.relation] ? `${RELATION_KO[link.relation]} ` : ''}` +
          `${EVIDENCE_KIND_KO[row.kind] || row.kind || link.evidence_id}${EVIDENCE_STATUS_KO[row.status] ? `(${EVIDENCE_STATUS_KO[row.status]})` : ''}`;
        kinds.set(label, (kinds.get(label) || 0) + 1);
        if (refused.has(`${sid}/${link.evidence_id}`)) marks.push(`거부된 근거 ${link.evidence_id}`);
      }
      if (unsupported.has(`${sid}/${claim.id}`)) marks.push('근거 잃음');
      if (!links.length) marks.push('연결된 근거 없음');
      rows.push({ step: sid, claim: claim.id, statement: String(claim.statement ?? ''),
        status: CLAIM_STATUS_KO[claim.status] || String(claim.status ?? ''),
        evidence: [...kinds].map(([label, n]) => (n > 1 ? `${label} ×${n}` : label)).join(', '), marks: marks.join(', ') });
    }
  }
  return rows;
}

function renderEvidenceTable(list, detail) {
  const rows = evidenceRows(detail);
  if (!rows.length) return;
  add(list, 'dt', `claim별 근거 (${rows.length}개)`);
  const cell = add(list, 'dd');
  const count = status => rows.filter(row => row.status === status).length;
  add(cell, 'p', [`지지 ${count('지지')}`, `부분 지지 ${count('부분 지지')}`,
    ...Object.values(CLAIM_STATUS_KO).filter(status => !['지지', '부분 지지'].includes(status) && count(status))
      .map(status => `${status} ${count(status)}`),
    `표시 있는 claim ${rows.filter(row => row.marks).length}`].join(' · '), 'decision-meta');
  const table = add(add(cell, 'div', '', 'evidence-wrap'), 'table', '', 'evidence-table');
  const head = add(add(table, 'thead'), 'tr');
  for (const label of ['단계', 'claim', '상태', '근거 종류', '표시']) add(head, 'th', label);
  const body = add(table, 'tbody');
  for (const row of rows) {
    const line = add(body, 'tr', '', row.marks ? 'flagged' : '');
    add(line, 'td', row.step);
    const claim = add(line, 'td');
    add(claim, 'div', row.statement);
    add(claim, 'small', row.claim, 'evidence-id');
    add(line, 'td', row.status);
    add(line, 'td', row.evidence || '-');
    add(line, 'td', row.marks || '-', row.marks ? '' : 'empty');
  }
}

const LETTERS = 'abcdefghijkl';
// The card adds the letter; an option written as "a) 승인" would read "a) a) 승인" (#331).
const optionText = o => o.trim().replace(/^\(?[a-lA-L]\)\s*(?=\S)/, '');
// A hard-stop question (R19) carries the employee's options (up to 12); the card renders them as one clarify question.
function cardQuestions(approval) {
  if (approval?.kind === 'clarify') return Array.isArray(approval.detail?.questions) ? approval.detail.questions : [];
  if (approval?.kind === 'question' && Array.isArray(approval.detail?.options))
    return [{ question: '직원에게 줄 답을 고르세요', options: approval.detail.options, allow_free_text: true }];
  return [];
}
// Structured CSO questions (#36): options become buttons; the composed answer is the approval note (#34).
function clarifyQuestions(approval) {
  const limit = approval?.kind === 'question' ? 12 : 4;
  // A hard-stop question keeps even a single option (AskRequest allows one): the PI must pick it, not approve blank.
  const least = approval?.kind === 'question' ? 1 : 2;
  const questions = cardQuestions(approval).filter(q => q && typeof q.question === 'string' && q.question.trim()).map(q => {
    const options = Array.isArray(q.options) ? q.options.filter(o => typeof o === 'string' && o.trim()).slice(0, limit).map(optionText) : [];
    return { question: q.question.trim(), options: options.length >= least ? options : [],
      allow_free_text: options.length < least || q.allow_free_text !== false, depth: [30, 60, 90].includes(q.depth) ? q.depth : null };
  });
  // A question without real choices keeps the plain answer box and its optional note.
  return approval?.kind === 'question' ? questions.filter(q => q.options.length) : questions;
}
// Approving without an answer is refused for clarify, and for a question card that offers choices (R19).
export function answerRequired(approval) {
  return approval?.kind === 'clarify' || (approval?.kind === 'question' && clarifyQuestions(approval).length > 0);
}
// CP2 수정 요청 re-plans from its note through a new CP1, so it needs one unless the continuation cap is reached (R10).
export function reviseContinues(approval) {
  return approval?.kind === 'research_evidence' && approval?.detail?.revise_continues !== false;
}
export function revisionNoteMissing(approval, choice, note) {
  return choice === 'revise' && reviseContinues(approval) && !String(note || '').trim()
    ? '수정 요청에는 고칠 점을 메모에 적어 주세요. 그 메모로 새 계획을 세웁니다.' : '';
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
    // A hard-stop question has one question: the employee gets the choice itself, without a "Q1." number.
    lines.push(row.dataset?.kind === 'question' ? picked.join(' — ') : `Q${index + 1}. ${picked.join(' — ')}`);
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

const DETAIL_LABELS = {
  budget: { spent_usd: '지금까지 쓴 비용', limit_usd: '지금 상한', requested_budget_usd: '승인하면 새 상한',
    unknown_count: '비용 미집계 작업', unknown_reserve_usd: '미집계 작업 1건당 가정 비용' },
  research_evidence: { ...EVIDENCE_LABELS, plan_sha256: 'plan hash', unreported_outputs: '보고하지 않은 산출',
    artifact_sha256: '산출 파일 hash', continuation: '이어 가기 차수' },
  research_plan: { target_sha256: 'plan hash', scope_status: '범위 판정', continuation: '이어 가기 차수' },
  research_continue: { round: '이어 가기 차수', limit: '이어 가기 상한', plan_sha256: '지난 계획 plan hash' },
  question: { why_blocked: '막힌 이유', from: '묻는 직원' },
  resume: { created_at: '접수 시각', steps: '남은 단계' },
};
// A resume card's epoch seconds read as a local date, the way the gateway writes it in the summary.
const two = n => String(n).padStart(2, '0');
function receivedAt(seconds) {
  const date = new Date(Number(seconds) * 1000);
  return Number.isFinite(date.getTime()) && seconds != null ?
    `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}` : String(seconds ?? '');
}
function renderDetail(container, kind, detail, approval = null) {
  const preferred = kind === 'tool_permission' ? ['tool_name', 'input'] :
    kind === 'hpc_submit' ? ['queue', 'script_path', 'script_preview', 'cores', 'mem', 'walltime', 'resources'] :
    kind === 'facilities_fix' ? ['action', 'reason', 'signature_id', 'command'] :
    kind === 'research_evidence' ? ['refused_rows', 'refused_evidence', 'unsupported_claims', 'results', 'plan_sha256'] :
    kind === 'research_plan' ? ['plan_canonical', 'continuation', 'target_sha256', 'scope_status'] :
    kind === 'research_continue' ? ['p1_issues', 'round', 'limit', 'plan_sha256'] :
    kind === 'question' ? ['why_blocked', 'from'] :
    kind === 'resume' ? ['steps', 'created_at'] : [];
  // Keys drawn elsewhere on the card, or repeated inside the frozen plan view (CP1, R18).
  const shown = kind === 'clarify' && Array.isArray(detail?.questions) ? ['questions', 'assumptions'] :
    kind === 'research_evidence' ? ['choices', 'gate', 'revise_continues'] :
    kind === 'research_continue' ? ['gate'] :
    kind === 'resume' ? ['request_text'] :  // the summary already quotes it
    kind === 'research_plan' && typeof detail?.plan_canonical === 'string' ? ['gate', 'pack_applicability', 'warnings', 'protocol_revision', 'packs'] :
    kind === 'question' && clarifyQuestions(approval).length ? ['options'] : [];
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
  const labels = DETAIL_LABELS[kind] || {};
  for (const [key, value] of entries) {
    if (kind === 'research_plan' && key === 'plan_canonical') renderResearchPlan(list, detail.plan_canonical);
    else if (kind === 'research_continue' && key === 'p1_issues') renderReviewIssues(list, detail.p1_issues);
    else if (kind === 'resume' && key === 'created_at') detailEntry(list, labels[key], receivedAt(detail.created_at), {raw: true});
    else if (kind === 'resume' && key === 'steps' && Array.isArray(detail.steps))
      detailEntry(list, labels[key], detail.steps.join(', ') || '없음', {raw: true});
    else if (kind === 'research_evidence' && key === 'results') {
      renderEvidenceTable(list, detail);
      detailEntry(list, '원장 JSON 전체', value, {raw: true, fold: true,
        summary: `원장 JSON 전체 (${value.length}자) · 단계 ${Object.keys(detail.results || {}).length}개`});
    } else if (kind === 'research_evidence' && key === 'artifact_sha256')
      detailEntry(list, labels[key], value, {raw: true, fold: true, summary: `산출 파일 hash (${Object.keys(detail.artifact_sha256 || {}).length}개)`});
    else if (kind === 'budget' && key.endsWith('_usd') && Number.isFinite(Number(detail[key])))
      detailEntry(list, labels[key] || key, `$${Number(detail[key]).toFixed(2)}`, {raw: true});  // not 25.860455119999997
    else detailEntry(list, labels[key] || key, value, {raw: true});
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
  const request = add(body, 'p', '', 'req-of');  // which request the card belongs to (R19)
  request.hidden = true;
  const summary = add(body, 'p', '', 'sum');
  const detail = add(body, 'div', '', 'why');
  const questions = add(body, 'div', '', 'clarify');
  questions.hidden = true;
  const assumptions = add(body, 'div', '', 'plan-assumptions');
  assumptions.hidden = true;
  const timing = add(body, 'p', '', 'decision-meta');
  const note = add(body, 'textarea', '', 'ans');
  note.rows = item.value.kind === 'clarify' ? 3 : 2;
  const consequence = add(body, 'p', '', 'decision-meta deny-note');  // what 거절 does, when the code fixes it
  consequence.hidden = true;
  const actions = add(body, 'div', '', 'acts');
  const approve = add(actions, 'button', '', 'btn go');
  approve.type = 'button';
  const revise = add(actions, 'button', '', 'btn');  // CP2 evidence review only
  revise.type = 'button'; revise.hidden = true;
  const deny = add(actions, 'button', '', 'btn');
  deny.type = 'button';
  row._decisionParts = { title, who, request, summary, detail, questions, assumptions, timing, note, consequence, approve, revise, deny };
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

// Cards the gateway never times out. None today: a resume card ends after policy.approvals.pi_decision_timeout_s too.
const NO_TIMEOUT_KINDS = new Set();
// What 거절 (or CP2 수정 요청) does, only where the gateway or CSO makes that outcome certain (R10, R19).
const DENY_RESULT = {
  resume: '거절하면 이 요청은 실패로 끝납니다.',
  research_plan: '거절하면 이 요청은 단계를 돌리지 않고 끝납니다. 계획을 고치려면 고칠 점을 넣어 새 요청을 보내세요.',
  research_evidence: '수정 요청은 메모로 새 계획을 세워 새 CP1을 받고 바뀐 단계만 다시 돌립니다. 거부하면 요청이 끝납니다.',
  question: '거절하면 직원에게 "진행 불가"로 전합니다.',
};
// Past research.revise_continuations a CP2 수정 요청 ends the request like 거부 (cso.py).
const EVIDENCE_REVISE_ENDS = '이어 가기 상한에 닿아 수정 요청과 거부는 둘 다 이 요청을 끝냅니다. 근거를 보강하려면 새 요청을 보내세요.';
// The gateway writes resume step ids as a Python list ("['s2', 's3']"); the card joins them (R19).
export function displaySummary(approval) {
  const text = String(approval?.summary ?? '');
  if (approval?.kind !== 'resume') return text;
  const ids = inner => [...inner.matchAll(/'([^']*)'/g)].map(match => match[1]);
  const whole = /^중단된 단계 \[([^\]]*)\]를 다시 돌릴까요\?$/.exec(text);
  if (whole) {
    const steps = ids(whole[1]);
    return steps.length === 1 && steps[0] === '요청' ? '중단된 요청을 다시 이어 갈까요?' : `중단된 단계(${steps.join(', ')})를 다시 돌릴까요?`;
  }
  return text.replace(/\[((?:'[^']*'(?:,\s*)?)+)\]/g, (_, inner) => ids(inner).join(', '));
}

function timingText(approval, options) {
  const now = options.now ? options.now() : Date.now() / 1000;
  const waited = Math.max(0, now - Number(approval.created_at || now));
  // Minutes up to two hours, then hours, then days: a 7-day decision card must not read as 10079분 (R5).
  const long = s => s < 172800 ? `${Math.floor(s / 3600)}시간` : `${Math.floor(s / 86400)}일`;
  const wait = waited < 60 ? `${Math.floor(waited)}초 대기` : waited < 7200 ? `${Math.floor(waited / 60)}분 대기`
    : `${long(waited)} 대기`;
  const left = Math.max(0, Number(approval.timeout_s || 0) - waited);
  const countdown = approval.timeout_s && !NO_TIMEOUT_KINDS.has(approval.kind)
    ? ` · ${left ? `${left < 7200 ? `${Math.ceil(left / 60)}분` : long(left)} 남음` : '시간 초과'}` : '';
  const blocked = blockedStepCount(approval, options.requests, options.taskStep);
  return `${wait}${countdown}${blocked ? ` · 하류 ${blocked}단계 멈춤` : ''}`;
}

function updateCard(row, item, options) {
  const value = item.value, p = row._decisionParts;
  row.dataset.kind = value.kind || item.type;
  const labels = options.kindLabels || {};
  const approval = item.type === 'approval';
  p.title.textContent = item.type === 'suggestion' ? '파견직 채용 제안' : (labels[value.kind] || value.kind || '결정');
  p.who.textContent = options.nick ? options.nick(value.agent_id) || 'CSO' : value.agent_id || 'CSO';
  // Parallel CP1/CP2 cards look alike; the request text tells them apart (data already in the web state).
  const request = approval && value.request_id && options.requests?.get ? options.requests.get(value.request_id) : null;
  p.request.textContent = request?.text ? `요청: ${short(request.text, 90)}` : '';
  p.request.hidden = !p.request.textContent;
  p.summary.textContent = item.type === 'suggestion' ? value.repo || value.paper || value.id : displaySummary(value) || value.id;
  renderDetail(p.detail, value.kind, item.type === 'suggestion' ? value.reason : value.detail, approval ? value : null);
  renderQuestions(p.questions, approval ? value : null);
  renderAssumptions(p.assumptions, approval ? value : null);
  p.timing.textContent = approval ? timingText(value, options) : '';
  p.note.placeholder = value.kind === 'question' ? (p.questions._questions?.length
    ? '덧붙일 말(선택)' : '직원에게 줄 답(선택). 비우고 승인하면 승인만 전합니다.')
    : reviseContinues(value) ? '메모(수정 요청이면 고칠 점을 꼭 적어 주세요)'
    : value.kind !== 'clarify' ? '메모(선택)' : p.questions._questions?.length
    ? '덧붙일 말(선택). 거절하면 요청을 멈춥니다.' : '답을 적어 주세요. 거절하면 요청을 멈춥니다.';
  p.consequence.textContent = !approval ? '' :
    value.kind === 'research_evidence' && !reviseContinues(value) ? EVIDENCE_REVISE_ENDS : DENY_RESULT[value.kind] || '';
  p.consequence.hidden = !p.consequence.textContent;
  const evidence = approval && value.kind === 'research_evidence';
  const scope = approval && value.kind === 'scope';  // out-of-scope request: run it or stop (#36)
  const answers = approval && clarifyQuestions(value).length > 0;
  p.approve.textContent = item.type === 'suggestion' ? '채용하기' : evidence ? '증거 승인' : value.kind === 'clarify' || (value.kind === 'question' && answers) ? '답하고 진행' : scope ? '진행' : '승인';
  p.deny.textContent = item.type === 'suggestion' ? '나중에' : evidence ? '거부' : scope ? '중단' : '거절';
  // CP2 수정 요청 re-plans through a new CP1 until the continuation cap, then ends the request like 거부 (R10).
  p.revise.textContent = reviseContinues(value) ? '수정 요청' : '수정 요청(요청 끝남)'; p.revise.hidden = !evidence;
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
      add(row, 'span', displaySummary(approval) || approval.id || '');
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
