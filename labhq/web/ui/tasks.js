const doc = () => globalThis.document;
function add(parent, tag, text = '', className = '') { const el = doc().createElement(tag); el.textContent = text; if (className) el.className = className; parent.append(el); return el; }
const STATUS = { pending:'대기', queued:'대기', working:'진행 중', waiting_quota:'한도 대기', waiting_login:'로그인 대기', waiting_facilities_fix:'환경 수정 승인 대기', hibernating:'HPC 대기', revise:'리뷰 반영', error:'실패', failed:'실패', skipped:'건너뜀', done:'완료' };
const COLUMN = { pending:'waiting', queued:'waiting', working:'working', waiting_quota:'working', waiting_login:'working', waiting_facilities_fix:'working', hibernating:'working', revise:'review', error:'review', failed:'review', skipped:'done', done:'done' };

function list(parent, title, values, className = '') {
  if (!values?.length) return;
  add(parent, 'strong', title, className);
  const ul = add(parent, 'ul');
  for (const value of values) add(ul, 'li', typeof value === 'string' ? value : `${value.problem || ''}${value.request ? ` — ${value.request}` : ''}`);
}

function cardFor(step, status, detail, options) {
  const card = add(doc().createElement('div'), 'details', '', `task-card task-${status}`);
  card.dataset.step = step.id;
  const summary = add(card, 'summary');
  add(summary, 'strong', step.id); add(summary, 'span', STATUS[status] || status);
  add(card, 'p', step.instruction || '', 'task-instruction');
  add(card, 'p', `${options.nick ? options.nick(step.agent_id) : step.agent_id || '미배정'} · 시도 ${detail.attempts || 0}회`, 'task-meta');
  list(card, '산출물', detail.outputs);
  list(card, '누락 산출물', detail.missing_outputs, 'missing');
  list(card, '리뷰 지적', detail.review_issues, 'review');
  // Environment failure (#35): what is missing on the runner PC and what to do, above the raw error.
  if (detail.environment) add(card, 'p', `환경 문제: ${detail.environment.cause || detail.environment.id || ''}${detail.environment.hint ? ` — ${detail.environment.hint}` : ''}`, 'task-error task-environment');
  if (detail.facilities_fix) {
    const fix = detail.facilities_fix, status = fix.status || (fix.ok ? 'succeeded' : 'failed');
    const label = status === 'applied' ? '적용' : status === 'succeeded' ? '성공' : '실패';
    add(card, 'p', `환경 수정 ${label}: ${(status === 'failed' ? fix.error : fix.action) || fix.fix_id || ''}`,
      status === 'failed' ? 'task-error' : 'task-meta');
  }
  if (detail.error) add(card, 'p', detail.error, 'task-error');
  if (detail.text) add(card, 'p', detail.text, 'task-result');
  if (status === 'waiting_quota') {
    const when = detail.quota_resume_at ? new Date(detail.quota_resume_at * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : '시각 확인 중';
    add(card, 'p', `한도 대기, ${when} 재개`, 'task-meta');
    if (options.onQuotaResume) {
      const resume = add(card, 'button', '지금 재개', 'btn'); resume.type = 'button';
      resume.addEventListener('click', () => options.onQuotaResume(step.id));
    }
  }
  if (status === 'waiting_login') {
    const when = detail.login_resume_at ? new Date(detail.login_resume_at * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : '시각 확인 중';
    add(card, 'p', `로그인 대기, ${when} 자동 재시도`, 'task-meta');
    if (detail.login_reason) add(card, 'p', detail.login_reason, 'task-error');
    if (options.onQuotaResume) {
      const resume = add(card, 'button', '로그인했어요 · 다시 시도', 'btn'); resume.type = 'button';
      resume.addEventListener('click', () => options.onQuotaResume(step.id));
    }
  }
  if (status === 'working' && detail.task_id && options.onCancel) {
    const cancel = add(card, 'button', '작업 취소', 'btn'); cancel.type = 'button';
    cancel.addEventListener('click', () => options.onCancel(detail.task_id));
  }
  return card;
}

export function syncTaskBoard(container, request, stepDetails, options = {}) {
  if (!request) { container.replaceChildren(add(doc().createElement('div'), 'p', '아직 요청이 없어요.', 'empty-note')); return; }
  const board = doc().createElement('div'); board.className = 'kanban';
  const columns = {};
  for (const [id, title] of [['waiting','대기'],['working','진행'],['review','확인'],['done','완료']]) {
    const column = add(board, 'section', '', 'kanban-column'); column.dataset.column = id; add(column, 'h3', title); columns[id] = column;
  }
  for (const step of request.plan || []) {
    const status = request.steps[step.id] || 'pending';
    const detail = stepDetails.get(`${request.id}:${step.id}`) || { attempts:0, outputs:[], missing_outputs:[], review_issues:[] };
    columns[COLUMN[status] || 'waiting'].append(cardFor(step, status, detail, options));
  }
  container.replaceChildren(board);
}
