// 2.5D history is independent of the shared reducer's short activity buffers.
export function messageFromEvent(ev) {
  const d = ev.data || {}, m = {seq:ev.seq, ts:ev.ts, rid:ev.request_id,
    who:ev.agent_id || 'cso', text:'', cls:''};
  switch (ev.type) {
    case 'agent.log':
      if (!d.text || ['debug','thinking'].includes(d.level)) return null;
      Object.assign(m, {text:d.text, cls:d.level === 'alert' ? 'alert' : '',
        truncated:d.text_truncated, cutChars:d.text_chars}); break;
    case 'agent.ask':
      Object.assign(m, {who:d.agent_id || m.who, to:String(d.to || '').replace(/^colleague:/,''), text:`질문: ${d.question || ''}`}); break;
    case 'agent.answer':
      Object.assign(m, {who:d.from || m.who, to:d.to, cls:d.status === 'rejected' ? 'alert' : '',
        text:`${d.status === 'rejected' ? '거절' : '답변'}: ${(d.status === 'rejected' ? d.reason : d.answer) || ''}${d.question ? ` (질문: ${d.question})` : ''}`}); break;
    case 'request.created': Object.assign(m, {who:'pi', text:`새 요청: ${d.text || ''}`}); break;
    case 'request.followup': Object.assign(m, {who:'pi', to:d.agent_id || 'cso', text:`이어 묻기: ${d.text || ''}`}); break;
    case 'request.note': Object.assign(m, {who:'pi', to:'cso', text:`실행 중 메모: ${d.text || ''}`}); break;
    case 'request.questions': Object.assign(m, {who:'cso', text:`확인이 필요해요: ${(d.questions || []).join(' / ')}`, cls:'alert'}); break;
    case 'approval.requested': Object.assign(m, {who:d.agent_id || m.who, text:`승인 요청: ${d.pi_summary || d.summary || ''}`, cls:'alert'}); break;
    case 'approval.resolved': {
      const closed = d.state === 'timed_out' ? '시간 초과로 닫혔어요' : ['expired','cancelled'].includes(d.state) ? '답 없이 닫혔어요' : '';
      Object.assign(m, {who:closed ? 'system' : 'pi', text:(closed || (d.approved ? '승인했어요' : d.choice === 'revise' ? '수정을 요청했어요' : '거절했어요')) + (d.note ? ` (${d.note})` : '')}); break;
    }
    case 'github.posted': Object.assign(m, {who:'github', text:'GitHub에 업데이트했어요', url:d.url}); break;
    case 'github.failed': Object.assign(m, {who:'github', text:`GitHub 업데이트 실패: ${d.error || ''}`, cls:'alert'}); break;
    case 'request.followup_done':
      Object.assign(m, {who:d.agent_id || m.who, to:'pi', cls:d.ok ? '' : 'alert',
        text:d.ok ? `답변: ${d.answer || ''}` : `이어 묻기에 답하지 못했어요: ${d.error || ''}`,
        truncated:d.answer_truncated, cutChars:d.answer_chars}); break;
    case 'request.completed': case 'request.failed':
      m.text = d.status === 'cancelled' ? '요청을 취소했어요. 끝난 단계까지 보고서에 남겼어요'
        : ev.type === 'request.completed' && d.ok !== false ? '최종 보고서를 올렸어요'
        : `요청을 완료하지 못했어요${d.error ? `: ${d.error}` : '. 실행 기록을 보세요'}`;
      m.cls = ev.type === 'request.failed' || d.ok === false ? 'alert' : ''; break;
    default: return null;
  }
  return m;
}

export function createMessenger({fetchPage}) {
  let rows = new Map(), generation = 0, followupAgents = new Map();
  const history = {
    items:[], before:null, loaded:false, loading:false, hasMore:true, error:false, onChange:() => {},
    get label() { return this.loading ? '불러오는 중…' : this.error ? '불러오지 못했어요. 눌러서 다시 시도하세요'
      : this.loaded && !this.hasMore ? '처음입니다' : '위로 스크롤하면 이전 기록을 불러옵니다'; },
    reset() {
      generation++; rows = new Map(); followupAgents = new Map();
      Object.assign(this, {items:[], before:null, loaded:false, loading:false, hasMore:true, error:false});
      this.onChange('reset');
    },
    ingest(ev) {
      if (ev.type === 'request.followup') followupAgents.set(ev.data.id, ev.data.agent_id);
      if (ev.type === 'request.followup_done') ev = {...ev, data:{...ev.data,
        agent_id:ev.data.agent_id || followupAgents.get(ev.data.id)}};
      const m = messageFromEvent(ev);
      if (!m || !Number.isSafeInteger(m.seq) || rows.has(m.seq)) return;
      rows.set(m.seq, m); this.items = [...rows.values()].sort((a,b) => a.seq-b.seq);
      this.onChange('live');
    },
    async loadOlder() {
      if (this.loading || !this.hasMore) return;
      const current = generation, initial = !this.loaded;
      this.loading = true; this.error = false; this.onChange('loading');
      try {
        const page = await fetchPage(this.before);
        if (current !== generation) return;
        for (const ev of page.events) {
          const m = messageFromEvent(ev);
          if (m) rows.set(m.seq, m); // full REST text replaces an overlapping live preview
        }
        this.items = [...rows.values()].sort((a,b) => a.seq-b.seq);
        this.before = page.next_before; this.hasMore = page.has_more; this.loaded = true;
      } catch (_) {
        if (current === generation) this.error = true;
      } finally {
        if (current === generation) { this.loading = false; this.onChange(initial ? 'initial' : 'older'); }
      }
    },
  };
  return history;
}

export function bindHistoryViewport(history, viewport, status, render) {
  let bottomPending = true;
  viewport.addEventListener('scroll', () => {
    if (viewport.scrollTop <= 24) return history.loadOlder();
  });
  status.onclick = () => history.loadOlder();
  return reason => {
    const height = viewport.scrollHeight, top = viewport.scrollTop;
    const atBottom = height - top - viewport.clientHeight <= 24;
    if (reason === 'reset' || reason === 'initial') bottomPending = true;
    render(); status.textContent = history.label;
    if (viewport.clientHeight > 0) {
      if (bottomPending || (reason === 'live' && atBottom)) {
        viewport.scrollTop = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
        bottomPending = false;
      } else if (reason === 'older') viewport.scrollTop = top + viewport.scrollHeight - height;
    }
  };
}

// Reuse rows: unfolding a message and loading older history never resets its disclosure.
export function syncMessageRows(container, items, options) {
  const old = container._rows || new Map(), next = new Map();
  const add = (parent, tag, cls) => {
    const el = document.createElement(tag); el.className = cls; parent.append(el); return el;
  };
  const rows = items.map(m => {
    let row = old.get(m);
    if (!row) {
      row = document.createElement('li'); row.className = `msg message-row ${m.who === 'pi' ? 'message-self' : ''} ${m.cls || ''}`;
      const avatar = add(row, 'span', 'av'); avatar.setAttribute('aria-hidden', 'true');
      const body = add(row, 'div', 'message-bubble');
      const header = add(body, 'div', 'message-header');
      const name = add(header, 'b', 'message-sender'), time = add(header, 'time', 'message-time');
      const text = add(body, 'div', 'message-text');
      options.expandableText(text, m.fullText ?? m.text, {pi:true, limit:150, summary:m.text,
        fold:m.fullText != null && m.fullText !== m.text, truncated:m.truncated, cutChars:m.cutChars});
      if (typeof m.url === 'string' && /^https?:\/\//.test(m.url)) {
        const link = add(text, 'a', 'message-link'); link.href=m.url; link.target='_blank'; link.rel='noopener'; link.textContent='열기';
      }
      row._messageParts = {avatar, name, time};
    }
    const p = row._messageParts;
    p.avatar.innerHTML = options.avatarHTML(m.who);
    p.name.textContent = options.nick(m.who) + (m.to ? ` → ${options.nick(m.to)}` : '');
    p.time.textContent = options.ago(m.ts);
    if (Number.isFinite(Number(m.ts))) p.time.setAttribute('datetime', new Date(Number(m.ts)*1000).toISOString());
    next.set(m,row); return row;
  });
  if (!items.length) {
    const empty = document.createElement('li'); empty.className='empty-note'; empty.textContent='팀이 주고받는 말이 여기에 쌓여요.';
    container.replaceChildren(empty);
  } else if (rows.some((row,i)=>container.children[i]!==row) || rows.length!==container.children.length) container.replaceChildren(...rows);
  container._rows=next; return rows;
}
