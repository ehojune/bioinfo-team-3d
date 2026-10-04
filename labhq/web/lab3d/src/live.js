import {syncDecisionCards} from '../../ui/decide.js';

// DOM controls and transport stay outside the shared event reducer.
export function startLiveOffice(onState) {
  const {S, apply, visual, fillRequestDetail, KIND_KO} = globalThis.LabHQState.createOfficeState();
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const readToken = () => { try { return localStorage.getItem('labhq_token') || ''; } catch { return ''; } };
  const saveToken = value => { try { localStorage.setItem('labhq_token', value); } catch {} };
  let token = params.get('token') || readToken(), lastSeq = 0, ws, retry, backoff = 1000;
  if (params.has('token')) {
    saveToken(token); params.delete('token');
    history.replaceState(null, '', location.pathname + (params.size ? `?${params}` : ''));
  }
  document.body.classList.add('live-mode');
  $('live-panel').hidden = false; $('demo-2d').hidden = true;
  document.querySelector('.demo').textContent = 'LIVE';
  $('state').disabled = true; $('randomize').hidden = true;
  const pending = new Set(), loadingAnswers = new Set();
  let noticeApproval = null;  // #184: the approval whose request the notice shows, cleared when it ends
  const notice = (value, approvalId = null) => { $('live-notice').textContent = value; noticeApproval = approvalId; };
  // #126: a snapshot holds the head of a long follow-up answer; the full request comes from the gateway on demand.
  async function loadFullAnswers(rid) {
    if (loadingAnswers.has(rid)) return;
    loadingAnswers.add(rid); render();
    try {
      const response = await fetch(`/api/requests/${encodeURIComponent(rid)}`, {headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) throw new Error(String(response.status));
      if (!fillRequestDetail(rid, await response.json())) notice('전문을 찾지 못했어요.');
    } catch (error) { notice(`전문을 불러오지 못했어요: ${error.message}`); }
    finally { loadingAnswers.delete(rid); render(); }
  }
  async function downloadAuditBundle(rid) {
    try {
      const response = await fetch(`/api/requests/${encodeURIComponent(rid)}/audit-bundle`,
        {headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) throw new Error(String(response.status));
      const url = URL.createObjectURL(await response.blob()), link = document.createElement('a');
      link.href = url; link.download = `labhq-audit-${rid}.zip`; link.click(); URL.revokeObjectURL(url);
    } catch (error) { notice(`감사 번들을 받지 못했어요: ${error.message}`); }
  }
  function text(parent, tag, value) {
    const el = document.createElement(tag); el.textContent = value; parent.append(el); return el;
  }
  function render() {
    $('connection').textContent = {live:'실시간', connecting:'연결 중', offline:'연결 끊김 · 재시도 중', auth:'토큰 확인 필요'}[S.conn];
    $('token-form').hidden = S.conn !== 'auth' && S.conn !== 'offline';
    $('approval-count').textContent = S.approvals.size;
    syncDecisionCards($('approvals'), S.approvals.values(), [], { tagName:'article', listTag:'div', kindLabels:KIND_KO,
      emptyText:'대기 승인 없음', disabled:a => S.conn !== 'live' || pending.has(a.id),
      onDecision:(a, approved, note, _type, choice) => {
        if (!ws || ws.readyState !== 1 || pending.has(a.id)) return;
        // Structured questions compose their answer in decide.js; an unanswered one yields ''.
        if (a.kind === 'clarify' && approved && !note) { notice(a.detail?.questions?.length ? '모든 질문에 답해 주세요.' : '답을 적어 주세요.'); return; }
        // CP2 evidence review carries its approve/revise/deny choice; the note never decides it (#90).
        ws.send(JSON.stringify(choice ? {type:'approval.resolve', id:a.id, approved, note, choice}
          : {type:'approval.resolve', id:a.id, approved, note}));
        pending.add(a.id); render();
      }});
    const requests = $('requests'), oldRows = new Map([...requests.children]
      .filter(row => row.dataset?.requestId).map(row => [row.dataset.requestId, row]));
    const rows = [];
    if (!S.requests.size) text({append: row => rows.push(row)}, 'p', '요청 없음');
    for (const q of [...S.requests.values()].reverse()) {
      const key = JSON.stringify([q.text, q.status, q.phase, q.plan, q.steps, q.review, q.error, q.report,
        q.report_appendix, q.report_truncated, q.report_appendix_truncated, q.followups, q.piNotes,
        loadingAnswers.has(q.id)]);
      const prior = oldRows.get(q.id);
      if (prior?.dataset.renderKey === key) { rows.push(prior); continue; }
      const row = document.createElement('article'); row.dataset.requestId = q.id; row.dataset.renderKey = key; rows.push(row);
      text(row, 'strong', q.text || q.id);
      text(row, 'p', `${q.status} · ${q.phase}`);
      const audit = text(row, 'a', '감사 번들');
      audit.href = `/api/requests/${encodeURIComponent(q.id)}/audit-bundle`;
      audit.onclick = event => { event.preventDefault(); downloadAuditBundle(q.id); };
      for (const step of q.plan) text(row, 'p', `${step.id} · ${step.instruction || ''} · ${q.steps[step.id] || 'pending'}`);
      if (q.review?.status === 'review_unparsed') text(row, 'p', '리뷰 판정 실패. PI 확인이 필요해요');
      if (q.error) text(row, 'p', q.error);
      if (q.report) {
        text(row, 'h4', 'PI용 보고서');
        text(row, 'pre', q.report + (q.report_truncated ? '…' : ''));
        if (q.report_truncated) {
          const more = text(row, 'button', '전문 보기'); more.type = 'button';
          more.disabled = loadingAnswers.has(q.id); more.onclick = () => loadFullAnswers(q.id);
        }
      }
      if (q.report_appendix) {
        const details = text(row, 'details', ''), summary = text(details, 'summary', '실행 기록');
        summary.textContent = '실행 기록';
        text(details, 'pre', q.report_appendix + (q.report_appendix_truncated ? '…' : ''));
        if (q.report_appendix_truncated) {
          const more = text(details, 'button', '전문 보기'); more.type = 'button';
          more.disabled = loadingAnswers.has(q.id); more.onclick = () => loadFullAnswers(q.id);
        }
      }
      for (const note of q.piNotes || []) {
        const sent = new Date(Number(note.at || 0) * 1000).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
        text(row, 'p', `실행 중 메모 · ${sent} · ${note.text}`);
      }
      for (const f of q.followups || []) {  // asked from the 2.5D request view; the shared reducer tracks them
        text(row, 'p', `이어 묻기: ${f.text} → ${f.status === 'done' ? f.answer + (f.answer_truncated ? '…' : '') : f.status === 'running' ? '답 기다리는 중' : f.error || '답하지 못함'}`);
        if (f.status === 'done' && f.answer_truncated) {
          const more = text(row, 'button', '전문 보기'); more.type = 'button';
          more.disabled = loadingAnswers.has(q.id); more.onclick = () => loadFullAnswers(q.id);
        }
      }
    }
    requests.replaceChildren(...rows);
    onState(S, visual);
  }
  function open() {
    clearTimeout(retry);
    if (ws) { ws.onclose = null; ws.onmessage = null; ws.close(); }
    if (!token) { S.conn = 'auth'; render(); return; }
    S.conn = 'connecting'; render();
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/client?token=${encodeURIComponent(token)}${lastSeq ? `&since=${lastSeq}` : ''}`);
    ws.onopen = () => { S.conn = 'live'; backoff = 1000; render(); };
    ws.onmessage = event => {
      try {
        const ev = JSON.parse(event.data);
        if (ev.type !== 'snapshot' && ev.seq && ev.seq <= lastSeq) return;
        for (const effect of apply(ev)) {
          if (effect.type === 'toast') notice(effect.text, effect.approval_id || null);
          if (effect.type === 'toast.clear' && noticeApproval && noticeApproval === effect.approval_id) notice('');
          if (effect.type === 'renderAfter') setTimeout(render, effect.ms);
        }
        if (ev.type === 'approval.stale') { pending.delete(ev.data?.id); notice('이미 끝난 승인 요청입니다.'); }
        if (ev.type === 'snapshot') { lastSeq = ev.seq || 0; pending.clear(); }
        else if (ev.seq && ev.seq > lastSeq) lastSeq = ev.seq;
        for (const id of pending) if (!S.approvals.has(id)) pending.delete(id);
        render();
      } catch (error) { console.warn('bad event', error); }
    };
    ws.onclose = event => {
      pending.clear(); S.conn = event.code === 1008 ? 'auth' : 'offline'; render();
      if (event.code !== 1008) { retry = setTimeout(open, backoff); backoff = Math.min(15000, backoff * 2); }
    };
  }
  $('token-form').addEventListener('submit', event => {
    event.preventDefault(); token = $('token').value.trim(); saveToken(token); lastSeq = 0; open();
  });
  setInterval(() => { if (S.approvals.size) render(); }, 1000);
  open();
}
