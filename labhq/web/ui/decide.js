const doc = () => globalThis.document;

export function plainPiText(value) {
  return String(value ?? '').replace(/```[^\n]*\n[\s\S]*?(?:```|$)|~~~[^\n]*\n[\s\S]*?(?:~~~|$)/g, '')
    .replace(/^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)/gm, '')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)').replace(/\*\*|__|`/g, '')
    .split('\n').filter(line => !/^[\s|:\-]+$/.test(line))
    .map(line => line.includes('|') ? line.replace(/^\s*\||\|\s*$/g, '').split('|').map(s=>s.trim()).join(' · ') : line)
    .join('\n').trim();
}

// Display only. Keep source and decision values intact, including numbers and option indices.
export function piText(parent, value, options = {}) {
  if (options.attribution) add(parent, 'small', options.attribution);
  const raw = String(options.original ?? value ?? ''), plain = plainPiText(value);
  const codeLine = /^\s*(?:\$[\w]+\s*=|import\s+\w+|from\s+\w+\s+import|(?:const|let|var|def|function|class)\s|print\s*\(|(?:Remove-Item|New-Item|Get-Content|python|powershell|rm|mkdir)\s|[\[{]\s*["'])/mi;
  const code = /```|~~~/.test(String(value ?? '')) || codeLine.test(String(value ?? ''));
  const english = /[A-Za-z]{3}/.test(plain) && !/[가-힣]/.test(plain);
  const prose = code ? plain.split('\n').filter(line => /[가-힣]/.test(line) && !codeLine.test(line) && !/[;{}=]/.test(line)).join('\n') : plain;
  const shown = english ? '직원이 영어로 보낸 내용입니다. 자세히에서 원문을 확인해 주세요.' :
    prose || (code ? '명령이나 코드가 포함된 내용입니다. 자세히에서 확인해 주세요.' : '');
  const preview = options.limit && shown.length > options.limit ? shown.slice(0, options.limit - 1) + '…' : shown;
  add(parent, options.tag || 'span', preview, 'pi-prose');
  if (code || english || preview !== shown || options.truncated || options.forceOriginal) {
    return expandableText(parent, raw, {...options, pi: false, fold: true, summary: '자세히'});
  }
  return parent;
}

function add(parent, tag, text = '', className = '') {
  const el = doc().createElement(tag);
  el.textContent = text;
  if (className) el.className = className;
  parent.append(el);
  return el;
}

// Native summary provides click, Enter and Space activation; only received text is used.
export function expandableText(parent, value, options = {}) {
  if (options.pi) return piText(parent, value, options);
  const text = String(value ?? ''), limit = options.limit ?? 240;
  const preview = text.replace(/\s+/g, ' ').trim();
  const cut = options.truncated ? ' (받은 내용 일부)' : '';
  if (!options.fold && preview.length <= limit && text.split('\n').length <= 8) {
    return add(parent, options.tag || 'span', text + cut);
  }
  const fold = add(parent, 'details', '', 'expandable-text');
  fold.open = Boolean(options.open);
  const summary = add(fold, 'summary', (options.summary || short(preview, limit)) + cut);
  const expanded = () => summary.setAttribute?.('aria-expanded', String(Boolean(fold.open)));
  fold.ontoggle = expanded; expanded();
  add(fold, 'pre', text);
  if (cut) add(fold, 'small', cut.trim());
  return fold;
}

// Do not replace unchanged controls: an open line and keyboard focus survive refreshes.
export function syncExpandableText(parent, value, options = {}) {
  const key = JSON.stringify([value, options]);
  if (parent._expandableKey === key) return;
  parent._expandableKey = key;
  parent.replaceChildren(); expandableText(parent, value, options);
}

export function createHistoryVisibility(scope, storage) {
  const key = `labhq_hidden_decisions:${scope}`;
  let hidden = new Set();
  const getStorage = () => storage === undefined ? globalThis.localStorage : storage;
  try {
    const saved = JSON.parse(getStorage()?.getItem(key) || '[]');
    if (Array.isArray(saved)) hidden = new Set(saved.filter(id => typeof id === 'string'));
  } catch {}
  const id = item => String(item.approval?.id || item.approval_id ||
    JSON.stringify([item.request_id, item.decided_at, item.approval?.summary]));
  return {
    has: item => hidden.has(id(item)),
    hide(item) {
      hidden.add(id(item));
      try { getStorage()?.setItem(key, JSON.stringify([...hidden])); } catch {}
    },
  };
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
    expandableText(cell, text, {...options, fold: true, limit: 120});
  } else piText(cell, text, options);
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
  const branches = Array.isArray(plan.branches) ? plan.branches : [];
  if (branches.length) {
    add(list, 'dt', `갈래 (${branches.length}개)`);
    const cell = add(list, 'dd'), table = add(add(cell, 'div', '', 'evidence-wrap'), 'table', '', 'evidence-table');
    const head = add(add(table, 'thead'), 'tr');
    for (const label of ['갈래', '질문', '자료', '단계 수', '추정 예산']) add(head, 'th', label);
    const body = add(table, 'tbody');
    for (const branch of branches) {
      const line = add(body, 'tr');
      const count = steps.filter(step => step.branch === branch.id).length;
      for (const value of [branch.id, branch.question, `${branch.evidence_type} · ${branch.data_boundary}`,
        `${count}개`, branch.estimated_budget_usd == null ? '미제공' : `$${Number(branch.estimated_budget_usd).toFixed(2)}`])
        add(line, 'td', value);
    }
  }
  detailEntry(list, '단계', steps.map(step => {
    const outputs = Array.isArray(step.outputs) ? step.outputs.join(', ') : '';
    return `${step.id} · ${step.agent_id || '미배정'} · ${step.instruction || ''}${outputs ? `\n    산출: ${outputs}` : ''}`;
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
      rows.push({ branch: detail?.step_branches?.[sid] || '미분류', step: sid,
        claim: claim.id, statement: String(claim.statement ?? ''),
        status: CLAIM_STATUS_KO[claim.status] || String(claim.status ?? ''),
        evidence: [...kinds].map(([label, n]) => (n > 1 ? `${label} ×${n}` : label)).join(', '), marks: marks.join(', ') });
    }
  }
  return rows;
}

function renderEvidenceTable(list, detail) {
  const rows = evidenceRows(detail);
  if (!rows.length) return;
  const branched = detail?.step_branches && Object.keys(detail.step_branches).length > 0;
  add(list, 'dt', `claim별 근거 (${rows.length}개)`);
  const cell = add(list, 'dd');
  const count = status => rows.filter(row => row.status === status).length;
  add(cell, 'p', [`지지 ${count('지지')}`, `부분 지지 ${count('부분 지지')}`,
    ...Object.values(CLAIM_STATUS_KO).filter(status => !['지지', '부분 지지'].includes(status) && count(status))
      .map(status => `${status} ${count(status)}`),
    `표시 있는 claim ${rows.filter(row => row.marks).length}`].join(' · '), 'decision-meta');
  const table = add(add(cell, 'div', '', 'evidence-wrap'), 'table', '', 'evidence-table');
  const head = add(add(table, 'thead'), 'tr');
  for (const label of [...(branched ? ['갈래'] : []), '단계', 'claim', '상태', '근거 종류', '표시']) add(head, 'th', label);
  const body = add(table, 'tbody');
  for (const row of rows) {
    const line = add(body, 'tr', '', row.marks ? 'flagged' : '');
    if (branched) add(line, 'td', row.branch);
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
// CP1 수정 요청 opens a new request from its note; a continuation round's CP1 has none (detail.revise_allowed).
export function reviseContinues(approval) {
  if (approval?.kind === 'research_plan') return approval?.detail?.revise_allowed === true;
  return approval?.kind === 'research_evidence' && approval?.detail?.revise_continues !== false;
}
export function revisionNoteMissing(approval, choice, note) {
  return choice === 'revise' && reviseContinues(approval) && !String(note || '').trim()
    ? '수정 요청에는 고칠 점을 메모에 적어 주세요. 그 메모로 새 계획을 세웁니다.' : '';
}
// What 수정 요청 does, for the toast after it is sent.
export function revisedToast(approval) {
  if (approval?.kind === 'research_plan') return '수정 요청을 보냈어요. 메모를 넣은 새 요청으로 계획을 다시 받아요';
  return reviseContinues(approval) ? '수정 요청을 보냈어요. 새 계획으로 CP1을 다시 받아요' : '수정 요청을 보냈어요. 이 요청은 끝나요';
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
    piText(add(block, 'legend'), `${index + 1}. ${question.question}`);
    if (question.depth) add(block, 'small', `깊이 약 ${question.depth}분`, 'clarify-depth');
    if (question.options.length) {
      const group = add(block, 'div', '', 'clarify-options');
      const buttons = question.options.map((option, choice) => {
        const button = add(group, 'button', `${LETTERS[choice]}) ${plainPiText(option)}`, 'btn opt');
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

export function storageChoice(row) {
  const box = row?._decisionParts?.storage;
  if (!box || box.hidden) return null;
  return {mode: box._mode.value, repo: box._repo.value.trim(), visibility: box._visibility.value,
    ...(box._progress ? {progress_push: box._progress.checked} : {})};
}

function renderStorage(box, value) {
  const config = value?.detail?.storage;
  box.hidden = !config;
  if (!config || box._config === JSON.stringify(config)) return;
  box._config = JSON.stringify(config);
  box.replaceChildren();
  add(box, 'strong', '결과를 GitHub 저장소로 정리할까요?');
  if (config.initial_label) add(box, 'p', `처음 선택: ${config.initial_label}`);
  if (config.warning) add(box, 'p', config.warning);
  const modeLabel = add(box, 'label', '저장 위치 '), mode = add(modeLabel, 'select');
  const choices = config.allow_keep ? [['keep','처음 선택 유지'], ['local','로컬만'],
    ['new','새 GitHub 저장소 만들기'], ['existing','기존 GitHub 저장소']] :
    [['local','로컬만 (기본)'], ['new','새 GitHub 저장소 만들기'], ['existing','기존 GitHub 저장소']];
  for (const [key, label] of choices) {
    const option = add(mode, 'option', label); option.value = key;
    option.disabled = !['keep','local'].includes(key) && !config.github_available;
  }
  mode.value = config.default || 'local';
  const repoLabel = add(box, 'label', '저장소 이름 '), repo = add(repoLabel, 'input');
  repo.value = config.suggested_name || '';
  const visibilityLabel = add(box, 'label', '공개 범위 '), visibility = add(visibilityLabel, 'select');
  for (const [key, label] of [['private','비공개'], ['public','공개 (누구나 볼 수 있음)']]) {
    const option = add(visibility, 'option', label); option.value = key;
  }
  visibility.value = 'private';
  let progress = null, progressLabel = null;
  if (config.progress_question) {
    progressLabel = add(box, 'label', '');
    progress = add(progressLabel, 'input'); progress.type = 'checkbox';
    progress.checked = Boolean(config.progress_default); progress.disabled = !config.progress_available;
    add(progressLabel, 'span', ` ${config.progress_question} (기본 꺼짐)`);
  }
  const update = () => {
    repoLabel.hidden = visibilityLabel.hidden = ['keep','local'].includes(mode.value);
    if (progressLabel) progressLabel.hidden = ['keep','local'].includes(mode.value);
    repo.placeholder = mode.value === 'existing' ? '소유자/저장소' : config.suggested_name;
  };
  mode.onchange = () => {repo.value = mode.value === 'new' ? config.suggested_name : ''; update();};
  box._mode = mode; box._repo = repo; box._visibility = visibility; box._progress = progress; update();
}

// CP2 evidence review (#90): the decision is a structured choice; the note is only a memo.
const EVIDENCE_LABELS = { refused_rows: '계약에 맞지 않아 뺀 근거',
  refused_evidence: '거부된 evidence (승인 대상 아님)', unsupported_claims: '근거를 잃은 claim',
  results: '단계별 claim·evidence 원장' };
export function decisionChoice(approval, act) {
  // CP1 sends a choice only where 수정 요청 exists, so an older gateway reads approve/deny exactly as before.
  if (approval?.kind !== 'research_evidence' && !(approval?.kind === 'research_plan' && reviseContinues(approval))) return null;
  return { approve: 'approve', revise: 'revise', deny: 'deny' }[act] || null;
}

const DETAIL_LABELS = {
  budget: { spent_usd: '지금까지 쓴 비용', limit_usd: '지금 상한', requested_budget_usd: '승인하면 새 상한',
    unknown_count: '비용 모름 작업', unknown_reserve_usd: '비용 모름 작업 1건당 가정 비용' },
  research_evidence: { ...EVIDENCE_LABELS, plan_sha256: 'plan hash', unreported_outputs: '보고하지 않은 산출',
    artifact_sha256: '산출 파일 hash', continuation: '이어 가기 차수' },
  research_plan: { target_sha256: 'plan hash', scope_status: '범위 판정', continuation: '이어 가기 차수' },
  recruit_plan: { recruits: '논문·채용 이유·담당 단계', activate_on_pass: '수습 통과하면 바로 투입', target_sha256: '채용 묶음 hash' },
  research_continue: { round: '이어 가기 차수', limit: '이어 가기 상한', plan_sha256: '지난 계획 plan hash' },
  question: { why_blocked: '막힌 이유', from: '묻는 직원' },
  resume: { created_at: '접수 시각', steps: '남은 단계' },
  result_publish: { pdf: '보고서 PDF' },
};
// A resume card's epoch seconds read as a local date, the way the gateway writes it in the summary.
const two = n => String(n).padStart(2, '0');
function receivedAt(seconds) {
  const date = new Date(Number(seconds) * 1000);
  return Number.isFinite(date.getTime()) && seconds != null ?
    `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}` : String(seconds ?? '');
}
export function renderDetail(container, kind, detail, approval = null) {
  if (['tool_permission', 'hpc_submit', 'budget', 'recruit'].includes(kind)) {
    const signature = JSON.stringify([kind, detail, approval?.summary, approval?.pi_reason, approval?.pi_outcome]);
    if (container._detailSignature === signature) return;
    container._detailSignature = signature;
    container.replaceChildren(); container.hidden = false;
    piText(add(container, 'p'), approval?.pi_reason || '실행 전에 PI의 확인이 필요합니다.');
    piText(add(container, 'p'), approval?.pi_outcome || '승인하면 제안된 작업을 진행합니다.');
    const fold = expandableText(container, '', {fold: true, summary: '자세히', truncated: detail?.input_truncated});
    // The existing field labels and exact received strings remain accessible inside one closed fold.
    fold.replaceChildren(fold.children[0]);
    const list = add(fold, 'dl', '', 'approval-detail');
    for (const [key, value] of Object.entries(detail || {})) {
      add(list, 'dt', DETAIL_LABELS[kind]?.[key] || key);
      const cell = add(list, 'dd');
      add(cell, 'pre', kind === 'budget' && key.endsWith('_usd') && Number.isFinite(Number(value))
        ? `$${Number(value).toFixed(2)}` : detailValue(value));
    }
    if (approval?.summary) add(fold, 'pre', approval.summary);
    return;
  }
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
    kind === 'resume' ? ['request_text', 'has_plan'] :  // the summary already quotes it; has_plan only picks the steps wording
    kind === 'research_plan' && typeof detail?.plan_canonical === 'string' ? ['gate', 'pack_applicability', 'warnings', 'protocol_revision', 'packs', 'revise_allowed'] :
    kind === 'question' && clarifyQuestions(approval).length ? ['options'] : [];
  shown.push('storage');
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
    if (kind === 'result_publish' && key === 'documents') {
      for (const [name, body] of Object.entries(detail.documents || {}))
        detailEntry(list, name, body, {raw:true, fold:true, open:name === 'README.md', summary:name});
    } else if (kind === 'result_publish' && key === 'images') {
      for (const [name, uri] of Object.entries(detail.images || {})) {
        if (!/^data:image\/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(uri)) continue;
        const figure = add(list, 'div'); add(figure, 'p', name);
        const img = add(figure, 'img'); img.src = uri; img.alt = name;
        img.setAttribute?.('style', 'max-width:100%;max-height:420px');
      }
    } else if (kind === 'result_publish' && key === 'image_preview_omitted') {
      for (const [name, item] of Object.entries(detail.image_preview_omitted || {})) {
        const size = Number(item?.size_bytes || 0);
        const label = `${name} · ${(size / 1024 / 1024).toFixed(1)} MB · ${String(item?.path || '')}`;
        detailEntry(list, name, label, {raw:true});
      }
    } else if (kind === 'research_plan' && key === 'plan_canonical') renderResearchPlan(list, detail.plan_canonical);
    else if (kind === 'research_continue' && key === 'p1_issues') renderReviewIssues(list, detail.p1_issues);
    else if (kind === 'resume' && key === 'created_at') detailEntry(list, labels[key], receivedAt(detail.created_at), {raw: true});
    else if (kind === 'resume' && key === 'steps' && Array.isArray(detail.steps))
      // No plan (a direct request, or a stop before planning) reruns the whole request; a card saved before has_plan reads 없음 (#521).
      detailEntry(list, labels[key], detail.steps.join(', ') || (detail.has_plan === false ? '요청 전체' : '없음'), {raw: true});
    else if (kind === 'research_evidence' && key === 'results') {
      renderEvidenceTable(list, detail);
      detailEntry(list, '원장 JSON 전체', value, {raw: true, fold: true,
        summary: `원장 JSON 전체 (${value.length}자) · 단계 ${Object.keys(detail.results || {}).length}개`});
    } else if (kind === 'research_evidence' && key === 'artifact_sha256')
      detailEntry(list, labels[key], value, {raw: true, fold: true, summary: `산출 파일 hash (${Object.keys(detail.artifact_sha256 || {}).length}개)`});
    else if (kind === 'budget' && key.endsWith('_usd') && Number.isFinite(Number(detail[key])))
      detailEntry(list, labels[key] || key, `$${Number(detail[key]).toFixed(2)}`, {raw: true});  // not 25.860455119999997
    else detailEntry(list, labels[key] || key, value, {raw: true, truncated: Boolean(detail[`${key}_truncated`])});
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
  const storage = add(body, 'div', '', 'result-storage');
  storage.hidden = true;
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
  row._decisionParts = { title, who, request, summary, detail, questions, storage, assumptions, timing, note, consequence, approve, revise, deny };
  if (options.presentation === 'journal') {
    row.className += ' ui-card decision-card';
    const heading = doc().createElement('h3'); heading.className = 'decision-question';
    const facts = doc().createElement('dl'); facts.className = 'decision-facts';
    const status = doc().createElement('span'); status.className = 'ui-chip decision-status';
    const dot = add(status, 'i', '', 'status-dot'); dot.setAttribute?.('aria-hidden', 'true');
    add(status, 'span', '결정 대기');
    const source = doc().createElement('details'); source.className = 'decision-source';
    add(source, 'summary', '자세히'); source.append(detail);
    const explanation = doc().createElement('div'); explanation.className = 'decision-explanation';
    const reason = add(explanation, 'p'), outcome = add(explanation, 'p');
    const memo = doc().createElement('label'); memo.className = 'decision-memo';
    add(memo, 'span', '메모'); memo.append(note);
    approve.className += ' ui-button ui-button--primary decision-primary';
    deny.className += ' ui-button decision-secondary';
    revise.className += ' ui-button decision-secondary';
    body.replaceChildren(heading, facts, summary, explanation, status, kind, request, questions, storage, assumptions, timing, consequence, actions, memo, source);
    Object.assign(row._decisionParts, {heading, facts, reason, outcome});
  }
  return row;
}

const DECISION_QUESTIONS = {
  hpc_submit: '클러스터 작업을 시작할까요?', tool_permission: '이 도구 실행을 승인할까요?',
  budget: '예산 상한을 올릴까요?', recruit: '이 직원을 채용할까요?',
  research_plan: '이 계획으로 연구를 시작할까요?', research_evidence: '이 근거를 승인할까요?',
  research_continue: '연구를 한 차례 더 이어 갈까요?', resume: '중단된 작업을 이어 갈까요?',
  clarify: '어떤 방향으로 진행할까요?', question: '직원에게 어떤 답을 줄까요?',
  scope: '이 범위로 진행할까요?', facilities_fix: '운영 문제 수정을 승인할까요?',
  result_storage: '결과를 GitHub 저장소로 정리할까요?', result_publish: '이 정리본을 올릴까요?',
};
// Facts come only from received fields; absent resource estimates must never be invented.
export function decisionFacts(value, who = '') {
  const d = value.detail || {}, missing = '미제공';
  const text = v => v == null || v === '' ? missing : String(v);
  const money = v => v != null && v !== '' && Number.isFinite(Number(v)) ? `$${Number(v).toFixed(2)}` : missing;
  switch (value.kind) {
    case 'hpc_submit': return [['자원', [d.cores != null ? `${d.cores}코어` : '', d.mem].filter(Boolean).join(' · ') || missing], ['최대 시간', text(d.walltime)], ['대기열', text(d.queue)]];
    case 'budget': return [['사용 비용', money(d.spent_usd)], ['현재 상한', money(d.limit_usd)], ['새 상한', money(d.requested_budget_usd)]];
    case 'research_plan': {
      let plan = {}; try { plan = JSON.parse(d.plan_canonical || '{}'); } catch {}
      const steps = Array.isArray(plan.steps) ? plan.steps : [];
      const people = new Set(steps.map(s => s.agent_id).filter(Boolean)).size;
      const branches = Array.isArray(plan.branches) ? plan.branches : [];
      return [['단계', Array.isArray(plan.steps) ? `${steps.length}개` : missing], ['담당', people ? `${people}명` : missing],
        ...(branches.length ? [['갈래', `${branches.length}개`]] : [])];
    }
    case 'research_evidence': return [['검토 단계', d.results ? `${Object.keys(d.results).length}개` : missing], ['근거를 잃은 주장', Array.isArray(d.unsupported_claims) ? `${d.unsupported_claims.length}개` : missing]];
    case 'research_continue': return [['이번 차수', text(d.round)], ['차수 상한', text(d.limit)]];
    case 'resume': return [['남은 단계', Array.isArray(d.steps) ? d.steps.join(', ') || (d.has_plan === false ? '요청 전체' : '없음') : missing], ['접수', receivedAt(d.created_at) || missing]];
    case 'tool_permission': return [['도구', text(d.tool_name)], ['요청 직원', d.requester_name || who || missing]];
    case 'recruit': return [['제안 직원', who || missing], ['출처', d.repo || value.repo ? '저장소 · 자세히' : '자세히에서 확인']];
    default: return [['요청 직원', who || missing], ['종류', (DECISION_QUESTIONS[value.kind] || '결정 요청').replace(/\?$/, '')]];
  }
}

function updateJournalCard(p, value, suggestion) {
  const kind = suggestion ? 'recruit' : value.kind;
  if (!/[가-힣]/.test(p.title.textContent)) p.title.textContent = '결정 요청';
  p.heading.textContent = DECISION_QUESTIONS[kind] || '이 요청을 승인할까요?';
  const facts = decisionFacts({...value, kind}, p.who.textContent);
  const signature = JSON.stringify(facts);
  if (p.facts._signature !== signature) {
    p.facts._signature = signature; p.facts.replaceChildren();
    for (const [label, value] of facts) {
      const cell = add(p.facts, 'div'); add(cell, 'dt', label); add(cell, 'dd', value);
    }
  }
  syncExpandableText(p.reason, value.pi_reason || value.detail?.reason || value.reason || '', {pi: true});
  syncExpandableText(p.outcome, value.pi_outcome || '', {pi: true});
  p.reason.hidden = !p.reason.textContent; p.outcome.hidden = !p.outcome.textContent;
  // These legacy renderers already fold their raw fields. Reuse that content inside the
  // journal's single 자세히 rather than making the PI open two identically named folds.
  if (['tool_permission', 'hpc_submit', 'budget', 'recruit'].includes(kind)) {
    const fold = [...p.detail.children].find(node => node.tagName?.toLowerCase() === 'details');
    if (fold) {
      const caption = fold.children[0]?.textContent || '';
      p.detail.replaceChildren(...[...fold.children].slice(1));
      if (caption.includes('받은 내용 일부')) add(p.detail, 'small', caption);
    }
  }
  // Raw values remain exact inside 자세히; field captions are readable Korean, including unknown keys.
  const captions = {reason:'이유', detail:'상세 내용', input:'명령 입력', tool_name:'도구', command:'명령',
    queue:'대기열', cores:'코어 수', mem:'메모리', walltime:'최대 시간', resources:'자원', repo:'저장소', paper:'논문',
    script_path:'스크립트 위치', script_preview:'스크립트 미리보기', action:'조치', signature_id:'문제 식별자',
    'data boundary':'데이터 경계', topics:'연구 주제', protocol:'연구 절차'};
  const visit = node => {
    if (node.tagName?.toLowerCase() === 'dt' && !/[가-힣]/.test(node.textContent)) node.textContent = captions[node.textContent] || '추가 정보';
    for (const child of node.children || []) visit(child);
  };
  visit(p.detail);
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
// What 채용하기 commits to: the hire's provenance is known only after conversion, so the card says what is recorded (#424).
const HIRE_RESULT = '채용하면 원본 commit·license·외부 전송 대상을 계약서와 명단 카드에 남겨요. 외부로 데이터를 보내는 파견직은 통제 데이터 단계를 맡지 않아요.';
const DENY_RESULT = {
  resume: '거절하면 이 요청은 실패로 끝납니다.',
  research_plan: '거절하면 이 요청은 단계를 돌리지 않고 끝납니다. 계획을 고치려면 고칠 점을 넣어 새 요청을 보내세요.',
  recruit_plan: '승인하면 수습 통과 뒤 이번 계획에 투입합니다. 거절·실패·시간 초과 때는 정규직이 맡고 계획에 이유를 남깁니다.',
  research_plan_revise: '수정 요청은 메모를 넣은 새 요청으로 계획을 다시 세워 새 CP1을 받습니다. 거절하면 이 요청은 단계를 돌리지 않고 끝납니다.',
  research_evidence: '수정 요청은 메모로 새 계획을 세워 새 CP1을 받고 바뀐 단계만 다시 돌립니다. 거부하면 요청이 끝납니다.',
  question: '거절하면 직원에게 "진행 불가"로 전합니다.',
};
// Past research.revise_continuations a CP2 수정 요청 ends the request like 거부 (cso.py).
const EVIDENCE_REVISE_ENDS = '이어 가기 상한에 닿아 수정 요청과 거부는 둘 다 이 요청을 끝냅니다. 근거를 보강하려면 새 요청을 보내세요.';
// The gateway writes resume step ids as a Python list ("['s2', 's3']"); the card joins them (R19).
export function displaySummary(approval) {
  if (approval?.pi_display?.status === 'translated') return approval.pi_display.text;
  if (approval?.pi_summary) return plainPiText(approval.pi_summary);
  const text = String(approval?.summary ?? '');
  const d = approval?.detail;
  if (approval?.kind === 'budget' && Number(d?.unknown_count) > 0 && Number.isFinite(Number(d?.spent_usd)) && Number.isFinite(Number(d?.limit_usd))) {
    const amount = value => `$${Number(value).toFixed(2)}`;
    const bound = Number(d.spent_usd) + Number(d.unknown_count) * Number(d.unknown_reserve_usd || 0);
    return `지금까지 ${amount(d.spent_usd)} 썼고, 비용을 아직 모르는 작업이 ${d.unknown_count}건 있어 상한 ${amount(d.limit_usd)}를 넘을 수 있습니다` +
      (d.unknown_reserve_usd != null ? ` (모르는 작업을 건당 ${amount(d.unknown_reserve_usd)}로 잡으면 ${amount(bound)}).` : '.') +
      (d.requested_budget_usd != null ? ` 승인하면 상한을 ${amount(d.requested_budget_usd)}로 올리고, 거절하면 새 작업을 보내지 않습니다.` : '');
  }
  if (approval?.kind !== 'resume') return plainPiText(text);
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
  if (options.avatarHTML && row.children[0]?.className === 'who') {
    const html = options.avatarHTML(value, item.type);
    if (row.children[0].innerHTML !== html) row.children[0].innerHTML = html;
  }
  row.dataset.kind = value.kind || item.type;
  const labels = options.kindLabels || {};
  const approval = item.type === 'approval';
  p.title.textContent = item.type === 'suggestion' ? '파견직 채용 제안' : (labels[value.kind] || value.kind || '결정');
  p.who.textContent = (value.kind === 'tool_permission' && value.detail?.requester_name) ||
    (options.nick ? options.nick(value.agent_id) || 'CSO' : value.agent_id || 'CSO');
  // Parallel CP1/CP2 cards look alike; the request text tells them apart (data already in the web state).
  const request = approval && value.request_id && options.requests?.get ? options.requests.get(value.request_id) : null;
  syncExpandableText(p.request, request?.text ? `요청: ${request.text}` : '', {limit: 90});
  p.request.hidden = !p.request.textContent;
  syncExpandableText(p.summary, item.type === 'suggestion' ? '필요한 일을 맡길 직원을 추가하려 합니다.' :
    value.kind === 'tool_permission' && !value.pi_summary ? '직원이 도구를 실행하려 합니다. 작업 내용은 자세히에서 확인해 주세요.' : displaySummary(value) || value.id,
      {pi: true, attribution: value.pi_display?.status === 'failed' ? '번역 못 함' : value.pi_explainer || value.pi_display ? '펭귄 비서실장 설명' : '',
        ...(value.pi_summary || value.kind === 'tool_permission' ? {} : {original: value.summary})});
  renderDetail(p.detail, item.type === 'suggestion' ? 'recruit' : value.kind,
    item.type === 'suggestion' ? {reason: value.reason, repo: value.repo, paper: value.paper} : value.detail,
    approval ? value : {pi_reason: plainPiText(value.reason), pi_outcome: HIRE_RESULT});
  if (p.heading) updateJournalCard(p, value, !approval);
  renderQuestions(p.questions, approval ? value : null);
  renderStorage(p.storage, approval ? value : null);
  renderAssumptions(p.assumptions, approval ? value : null);
  p.timing.textContent = approval ? timingText(value, options) : '';
  p.note.placeholder = value.kind === 'question' ? (p.questions._questions?.length
    ? '덧붙일 말(선택)' : '직원에게 줄 답(선택). 비우고 승인하면 승인만 전합니다.')
    : reviseContinues(value) ? '메모(수정 요청이면 고칠 점을 꼭 적어 주세요)'
    : value.kind !== 'clarify' ? '메모(선택)' : p.questions._questions?.length
    ? '덧붙일 말(선택). 거절하면 요청을 멈춥니다.' : '답을 적어 주세요. 거절하면 요청을 멈춥니다.';
  p.consequence.textContent = !approval ? HIRE_RESULT :
    value.kind === 'research_evidence' && !reviseContinues(value) ? EVIDENCE_REVISE_ENDS :
    value.kind === 'research_plan' && reviseContinues(value) ? DENY_RESULT.research_plan_revise : DENY_RESULT[value.kind] || '';
  p.consequence.hidden = !p.consequence.textContent;
  const evidence = approval && value.kind === 'research_evidence';
  const planRevise = approval && value.kind === 'research_plan' && reviseContinues(value);  // CP1 수정 요청
  const scope = approval && value.kind === 'scope';  // out-of-scope request: run it or stop (#36)
  const answers = approval && clarifyQuestions(value).length > 0;
  p.approve.textContent = item.type === 'suggestion' ? '채용하기' : evidence ? '증거 승인' : value.kind === 'clarify' || (value.kind === 'question' && answers) ? '답하고 진행' : scope ? '진행' : '승인';
  p.deny.textContent = item.type === 'suggestion' ? '나중에' : evidence ? '거부' : scope ? '중단' : '거절';
  if (value.kind === 'result_storage') {p.approve.textContent = '선택하고 진행'; p.deny.textContent = '로컬만';}
  if (value.kind === 'result_publish') {p.approve.textContent = '확인했어요 · 올리기'; p.deny.textContent = '로컬에만 남기기';}
  // CP2 수정 요청 re-plans through a new CP1 until the continuation cap, then ends the request like 거부 (R10).
  p.revise.textContent = reviseContinues(value) ? '수정 요청' : '수정 요청(요청 끝남)'; p.revise.hidden = !(evidence || planRevise);
  p.approve.dataset.act = item.type === 'suggestion' ? 'hire' : 'approve';
  p.revise.dataset.act = 'revise';
  p.deny.dataset.act = item.type === 'suggestion' ? 'later' : 'deny';
  const disabled = options.disabled ? options.disabled(value, item.type) : false;
  const reviewPending = value.kind === 'result_publish' && request?.pi_review_pending === true && !request.pi_reviewed_at;
  p.approve.disabled = disabled || reviewPending; p.revise.disabled = disabled; p.deny.disabled = disabled;
  if (reviewPending) {
    p.consequence.textContent = '보고서 검토·확인을 완료하면 정리본을 올릴 수 있어요.';
    p.consequence.hidden = false;
  }
  if (options.onDecision) {
    p.approve.onclick = () => options.onDecision(value, true, decisionNote(row, true), item.type, decisionChoice(value, 'approve'), storageChoice(row));
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

// CP4 uses this same report card; its target travels with the PI's decision.
export function syncReviewCards(container, requests, options = {}) {
  const existing = new Map([...container.children].map(row => [row.dataset.reviewRequest, row]));
  const rows = [...requests].filter(q => globalThis.LabHQState.needsPiReview(q) || globalThis.LabHQState.needsStorage(q)).map(q => {
    const reviewPending = globalThis.LabHQState.needsPiReview(q);
    const final = q.status === 'review_pending' ? q.final_checkpoint : null;
    const reportReady = q.status === 'done' || !!final;
    const key = JSON.stringify([q.text, q.status, final, q.resultStorage, reviewPending, options.disabled?.(q) || false]);
    const prior = existing.get(q.id);
    if (prior?.dataset.reviewKey === key) return prior;
    const row = doc().createElement('article'); row.className = 'ap';
    row.dataset.reviewRequest = q.id; row.dataset.reviewKey = key;
    const body = add(row, 'div');
    const journal = options.presentation === 'journal';
    if (journal) {
      row.className += ' ui-card decision-card';
      add(body, 'h3', !reviewPending ? '결과를 어디에 저장할까요?' : reportReady ? '보고서를 검토할까요?' : '종료된 요청의 결과를 확인할까요?', 'decision-question');
      const facts = add(body, 'dl', '', 'decision-facts');
      for (const [label, value] of [['상태', globalThis.LabHQState.reviewLabel(q)], ['자동 승인', '없음']]) {
        const cell = add(facts, 'div'); add(cell, 'dt', label); add(cell, 'dd', value);
      }
      const status = add(body, 'span', '', 'ui-chip decision-status');
      add(status, 'i', '', 'status-dot').setAttribute?.('aria-hidden', 'true');
      add(status, 'span', reviewPending ? '검토 대기' : '저장 위치 미정');
    }
    add(body, 'strong', reviewPending ? globalThis.LabHQState.reviewLabel(q) : '결과 저장', 'kind');
    add(body, 'p', q.text || q.id, 'sum');
    add(body, 'p', !reviewPending ? '저장 위치를 고르면 결과 정리와 게시 승인으로 이어집니다.' : final ? 'CP4 · 최종 초안을 검토해 주세요. 수용은 게시·전송·실행 승인이 아닙니다.' : reportReady ? '작업 완료 · 보고서를 검토해 주세요.' : '요청 종료 · 결과와 이유를 확인해 주세요.', 'decision-meta');
    if (final) add(body, 'p', `초안 ${final.revision} · ${final.target_sha256} · 기한 ${new Date(final.deadline_at * 1000).toLocaleString()}`, 'decision-meta');
    const actions = add(body, 'div', '', 'acts');
    const open = add(actions, 'button', reportReady ? '보고서 열기' : '결과·이유 보기', 'btn go');
    if (journal) open.className += ' ui-button ui-button--primary decision-primary';
    open.type = 'button'; open.onclick = () => options.onOpen?.(q.id);
    const disabled = options.disabled?.(q) || false;
    if (reviewPending) {
      const done = add(actions, 'button', reportReady ? '검토 완료' : '확인 완료', 'btn');
      done.type = 'button'; done.disabled = disabled;
      done.onclick = () => options.onDone?.(q.id, final ? {decision:'accepted', revision:final.revision, target_sha256:final.target_sha256} : {});
      if (final) {
        const deny = add(actions, 'button', '수용 거부', 'btn'); deny.type = 'button'; deny.disabled = disabled;
        deny.onclick = () => options.onDone?.(q.id, {decision:'rejected', revision:final.revision, target_sha256:final.target_sha256});
      }
    }
    if (globalThis.LabHQState.needsStorage(q)) {
      const storage = add(actions, 'button', '저장 위치 정하기', 'btn');
      storage.type = 'button'; storage.disabled = disabled;
      storage.onclick = () => options.onStorage?.(q.id);
    }
    return row;
  });
  placeRows(container, rows);
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
  const all = Array.from(history || []);
  const visibility = options.visibility || (container._historyVisibility ||= createHistoryVisibility(options.scope || 'live'));
  const items = all.filter(item => !visibility.has(item));
  const pageSize = Math.max(1, Number(options.pageSize) || 10);
  if (!Number.isFinite(container._historyShown) || container._historyShown < pageSize) container._historyShown = pageSize;
  const key = JSON.stringify([items, container._historyShown, items.map(item => {
    const request = options.requests?.get?.(item.approval?.request_id || item.request_id);
    return [request?.text, request?.report];
  })]);
  if (container._historyKey === key) return;
  container._historyKey = key;
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
    const label = request?.text || (requestId === 'other' ? '기타 요청' : `요청 ${requestId}`);
    expandableText(add(group, 'h3'), label, {limit: 44});
    const list = add(group, 'ol');
    for (const item of values) {
      const approval = item.approval || {};
      const row = add(list, 'li', '', 'decision-history-item');
      if (options.presentation === 'journal') row.className += ' ui-card ui-list-row';
      const outcome = item.state === 'reconciled' ? '저장 선택 복구' : item.state === 'timed_out' ? '시간 초과' : item.state === 'expired' ? '만료' : item.approved ? '승인' :
        item.choice === 'revise' ? '수정 요청' : '거절';
      add(row, 'strong', `${outcome} · ${(options.kindLabels || {})[approval.kind] || approval.kind || '결정'}`);
      if (options.presentation === 'journal') {
        row.children[0].className = 'decision-history-status';
        add(row.children[0], 'i', '', 'status-dot').setAttribute?.('aria-hidden', 'true');
      }
      const at = new Date(Number(item.decided_at) * 1000);
      if (item.decided_at != null && Number.isFinite(at.getTime())) {
        const pad = n => String(n).padStart(2, '0');
        const time = add(row, 'time', `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`);
        time.dateTime = at.toISOString();
      }
      expandableText(row, displaySummary(approval) || approval.id || '', {limit: 240});
      if (item.note) expandableText(row, item.note, {limit: 160, tag: 'small'});
      if (request?.report) {
        const link = add(row, 'a', '요청 보고서', 'decision-report');
        link.href = `#request-${encodeURIComponent(requestId)}`;
        if (options.onOpenRequest) link.onclick = event => { event.preventDefault(); options.onOpenRequest(requestId); };
      }
      if (!['open', 'pending'].includes(item.state)) {
        const hide = add(row, 'button', '×', 'decision-hide'); hide.type = 'button';
        hide.setAttribute?.('aria-label', '이 결정 목록에서 숨기기');
        hide.onclick = () => { visibility.hide(item); syncDecisionHistory(container, all, {...options, visibility}); };
      }
    }
    root.append(group);
  }
  if (!items.length) add(root, 'p', '아직 결정 이력이 없어요.', 'empty-note');
  const children = [root];
  if (visible.length < items.length) {
    const more = doc().createElement('button'); more.type = 'button'; more.className = 'btn decision-history-more';
    more.textContent = `더 보기 (${items.length - visible.length}건)`;
    more.onclick = () => { container._historyShown += pageSize; syncDecisionHistory(container, all, {...options, visibility}); };
    children.push(more);
  }
  container.replaceChildren(...children);
}
