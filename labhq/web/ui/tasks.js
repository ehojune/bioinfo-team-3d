const doc = () => globalThis.document;
function add(parent, tag, text = '', className = '') { const el = doc().createElement(tag); el.textContent = text; if (className) el.className = className; parent.append(el); return el; }
const STATUS = { pending:'대기', queued:'대기', working:'진행 중', revise:'리뷰 반영', error:'실패', failed:'실패', skipped:'건너뜀', done:'완료' };
const COLUMN = { pending:'waiting', queued:'waiting', working:'working', revise:'review', error:'review', failed:'review', skipped:'done', done:'done' };

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
  if (detail.error) add(card, 'p', detail.error, 'task-error');
  if (detail.text) add(card, 'p', detail.text, 'task-result');
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
