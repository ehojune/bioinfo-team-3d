const doc = () => globalThis.document;
function add(parent, tag, text = '', className = '') { const el = doc().createElement(tag); el.textContent = text; if (className) el.className = className; parent.append(el); return el; }

function statusOf(state, agent = {}) {
  // An unanswered ask hibernates too; only HPC jobs are 'HPC 수면' (state.js stateText).
  if (state === 'hibernating' && agent.waitFor === 'answer') return '답 기다림';
  return state === 'working' ? '일함' : state === 'waiting' ? '내 답 기다림' : state === 'hibernating' ? 'HPC 수면' :
    state === 'done' ? '완료' : state === 'error' ? '오류' : state === 'queued' ? '순서 대기' : '대기';
}

// #424: what a contract hire was built from and where its tools send data; '' for core staff or an older roster.
export function provenanceText(agent = {}) {
  const p = agent.contract_provenance;
  // On probation the hire is on the roster but gets no work until the PI runs `labhq contract activate`.
  const hold = agent.contract_status === 'probation' ? ['수습(PI 승인 대기)'] : [];
  if (!p) return hold.join('');
  const hosts = (p.external_services || []).map(s => s && s.host).filter(Boolean);
  const parts = [p.source_commit ? `commit ${String(p.source_commit).slice(0, 7)}` : 'commit 미상',
    p.code_license ? `code ${p.code_license}` : 'license 미상'];
  if (p.weights_license) parts.push(`weights ${p.weights_license}`);
  parts.push(hosts.length ? `외부 전송 ${hosts.join(', ')}` : '외부 전송 없음');
  if ((p.rehire_changed || []).length) parts.push(`재고용 때 바뀜: ${p.rehire_changed.join(', ')}`);
  return [...hold, ...parts].join(' · ');
}

function makeCard(agent) {
  const card = doc().createElement('button'); card.type = 'button'; card.className = 'staff-card'; card.dataset.agent = agent.id;
  const top = add(card, 'span', '', 'staff-card-top');
  const name = add(top, 'strong'); const status = add(top, 'em');
  const role = add(card, 'span', '', 'staff-role');
  const tool = add(card, 'span', '', 'staff-tool');
  const provenance = add(card, 'span', '', 'staff-provenance');
  const gaugeLabel = add(card, 'span', '', 'staff-gauge-label');
  const gauge = add(card, 'progress', '', 'staff-gauge');
  card._staffParts = { name, status, role, tool, provenance, gaugeLabel, gauge };
  return card;
}

function update(card, agent, options) {
  const p = card._staffParts, state = options.visual ? options.visual(agent) : agent.state || 'idle';
  p.name.textContent = agent.name || agent.id; p.status.textContent = statusOf(state, agent);
  p.role.textContent = agent.role || '역할 미지정';
  p.tool.textContent = `도구 · ${agent.tool ? (options.toolLabel ? options.toolLabel(agent.tool) : agent.tool) : '없음'}`;
  p.provenance.textContent = provenanceText(agent); p.provenance.hidden = !p.provenance.textContent;
  p.provenance.title = p.provenance.textContent;
  const turns = Number(agent.usage?.num_turns ?? agent.toolCalls ?? 0), maxTurns = Number(agent.max_turns || 0);
  const now = options.now ? options.now() : Date.now() / 1000;
  const elapsed = state === 'working' ? Math.max(0, now - Number(agent.stateAt || now)) : 0;
  const timeout = Number(agent.task_timeout_s || 0);
  if (maxTurns > 0) { p.gaugeLabel.textContent = `턴 · ${turns}/${maxTurns}`; p.gauge.max = maxTurns; p.gauge.value = Math.min(turns, maxTurns); }
  else { p.gaugeLabel.textContent = timeout ? `경과 · ${Math.floor(elapsed)}초/${timeout}초` : `경과 · ${Math.floor(elapsed)}초/제한 미상`; p.gauge.max = timeout || 1; p.gauge.value = timeout ? Math.min(elapsed, timeout) : 0; }
  card.className = `staff-card staff-${state}`;
  card.setAttribute('aria-label', `${agent.name || agent.id}, ${statusOf(state, agent)}, ${p.tool.textContent}, ${p.gaugeLabel.textContent}`);
}

export function syncStaffCards(container, agents, options = {}) {
  const existing = new Map(Array.from(container.children || []).map(card => [card.dataset.agent, card]));
  const cards = Array.from(agents || []).map(agent => { const card = existing.get(agent.id) || makeCard(agent); update(card, agent, options); return card; });
  container.replaceChildren(...cards);
  return cards;
}
