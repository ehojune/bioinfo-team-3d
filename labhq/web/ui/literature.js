// #660: the fox's paper list. Rows come from GET /api/requests/{id} `paper_literature`; text only, links from checked IDs.
const doc = () => globalThis.document;
function add(parent, tag, text = '', className = '') { const el = doc().createElement(tag); el.textContent = text; if (className) el.className = className; parent.append(el); return el; }

const USE = {planning: '계획 참고', paper_skill: '자문 스킬', recruit: '재현 채용'};
const SKILL = {deferred: '승인 뒤 준비 예정', ready: '생성됨', reused: '생성됨(재사용)', failed: '생성 실패',
  unavailable: '생략(준비 늦음·불가)', skipped: '생략', unknown: '알 수 없음'};
// A result reference is attribution only: never a tool call or a scientific benefit.
const REFERENCE = {referenced: '결과에서 참조 확인(도구 호출·효용 증거 아님)', unverified: '참조 미확인', not_dispatched: '단계 실행 전'};
const REASON = {input_changed: '입력 변경', step_failed: '단계 실패', no_result_reference: '결과에 참조 없음'};

export function paperHref(id) {
  const value = String(id || '');
  const doi = /^doi:(10\.\d{4,9}\/[^\s<>"'\\]+)$/.exec(value);
  if (doi) return `https://doi.org/${encodeURI(doi[1])}`;
  const pmid = /^pmid:(\d{1,10})$/.exec(value);
  return pmid ? `https://pubmed.ncbi.nlm.nih.gov/${pmid[1]}/` : '';
}

export function usesText(row) {
  const uses = (Array.isArray(row?.uses) ? row.uses : []).map(use => USE[use]).filter(Boolean);
  return uses.length ? uses.join(' · ') : '미사용';
}

export function summaryText(view) {
  if (!view || typeof view !== 'object') return '선행 연구 기록을 불러오지 못했어요.';
  if (view.status === 'absent') return '이 요청에는 선행 연구 기록이 없어요.';
  const papers = Array.isArray(view.papers) ? view.papers : [];
  const count = use => papers.filter(row => Array.isArray(row?.uses) && row.uses.includes(use)).length;
  const head = view.status === 'ok' ? `선행 연구 ${count('planning')}편` : `선행 연구 조사 결과 없음${view.warning ? ` (${view.warning})` : ''}`;
  return [head, `자문 스킬 ${count('paper_skill')}`, `재현 채용 ${count('recruit')}`].join(' · ') +
    (view.damaged ? ' · 일부 스킬 기록이 손상돼 표시하지 않았어요' : '');
}

// Other requests' selections of the same skill (#660). Only shown when the gateway sent them, so an older gateway shows no row.
function previousUsesText(skill, history) {
  const uses = (Array.isArray(skill.previous_uses) ? skill.previous_uses : []).filter(use => use && typeof use === 'object');
  const total = Number.isFinite(skill.previous_use_count) ? skill.previous_use_count : uses.length;
  const basis = history && Number.isFinite(history.limit)
    ? `최근 ${history.scanned ?? history.limit}건 기준${history.truncated ? ', 더 오래된 요청은 세지 않음' : ''}` : '';
  if (!total) return `다른 요청에서 고른 기록 없음${basis ? ` (${basis})` : ''}`;
  const day = use => (Number.isFinite(use.created_at) ? new Date(use.created_at * 1000).toISOString().slice(0, 10) : '날짜 없음');
  const rows = uses.map(use => `${String(use.request_id).slice(0, 40)} ${day(use)} · 단계 ${(Array.isArray(use.attached_steps) ? use.attached_steps : []).join(', ') || '없음'}` +
    ` · ${REFERENCE[use.reference] || REFERENCE.unverified}`);
  return `${total}건${basis ? ` (${basis})` : ''}: ${rows.join(' / ')}${total > uses.length ? ` / 외 ${total - uses.length}건` : ''}`;
}

export function skillFacts(skill, history = null) {
  if (!skill || typeof skill !== 'object') return [];
  const version = [skill.skill_sha256 ? `skill ${String(skill.skill_sha256).slice(0, 12)}` : 'skill hash 없음',
    skill.paper2agent_commit ? `Paper2Agent ${String(skill.paper2agent_commit).slice(0, 7)}` : 'Paper2Agent commit 미기록'].join(' · ');
  const reasons = (skill.reference_reasons || []).map(code => REASON[code]).filter(Boolean);
  const steps = list => (Array.isArray(list) && list.length ? list.join(', ') : '없음');
  return [
    ['스킬로 만든 것', skill.selection_reason || '이유 미기록'],
    ['상태', (SKILL[skill.status] || SKILL.unknown) + (skill.reason ? ` (${skill.reason})` : '')],
    ['생성 버전', skill.built ? version : '생성 안 됨'],
    ['붙을 단계', steps(skill.selected_steps) + (skill.agents?.length ? ` → ${skill.agents.join(', ')}` : '')],
    ['붙은 단계', steps(skill.attached_steps)],
    ['결과 참조', (REFERENCE[skill.reference] || REFERENCE.unverified) + (reasons.length ? ` (${reasons.join(', ')})` : '')],
    ...(Array.isArray(skill.previous_uses) ? [['이전 사용', previousUsesText(skill, history)]] : []),
  ];
}

export function syncPaperLiterature(container, view, {loading = false} = {}) {
  container.replaceChildren();
  add(container, 'p', loading ? '선행 연구 목록을 불러오는 중이에요.' : summaryText(view), 'paper-literature-summary');
  if (loading || !view || !Array.isArray(view.papers)) return [];
  return view.papers.map(row => {
    const item = add(container, 'details', '', 'paper-row');
    add(item, 'summary', `${row.title || '제목 없음'} · ${usesText(row)}`);
    const facts = add(item, 'dl', '', 'facts');
    const fact = (label, value) => { add(facts, 'dt', label); return add(facts, 'dd', value); };
    const ids = fact('식별자', (row.ids || []).length ? '' : '없음');
    (row.ids || []).forEach((id, index) => {
      if (index) add(ids, 'span', ' · ');
      const href = paperHref(id);
      if (!href) { add(ids, 'span', id); return; }
      const link = add(ids, 'a', id); link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    });
    fact('쓰임', usesText(row));
    for (const [label, value] of skillFacts(row.skill, view.history)) fact(label, value);
    if (row.recruit) {
      const hire = row.recruit;
      fact('재현 채용', `${hire.status || '알 수 없음'}${hire.agent_id ? ` · ${hire.agent_id}` : ''}` +
        `${(hire.step_ids || []).length ? ` · 단계 ${hire.step_ids.join(', ')}` : ''}`);
    }
    return item;
  });
}
