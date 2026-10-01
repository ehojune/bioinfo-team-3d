import {syncDecisionCards} from '../../ui/decide.js';

// DOM controls and transport stay outside the shared event reducer.
export function startLiveOffice(onState) {
  const {S, apply, visual, fillFollowups, KIND_KO} = globalThis.LabHQState.createOfficeState();
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
  // #126: a snapshot holds the head of a long follow-up answer; the full request comes from the gateway on demand.
  async function loadFullAnswers(rid) {
    if (loadingAnswers.has(rid)) return;
    loadingAnswers.add(rid); render();
    try {
      const response = await fetch(`/api/requests/${encodeURIComponent(rid)}`, {headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) throw new Error(String(response.status));
      if (!fillFollowups(rid, await response.json())) $('live-notice').textContent = '전문을 찾지 못했어요.';
    } catch (error) { $('live-notice').textContent = `전문을 불러오지 못했어요: ${error.message}`; }
    finally { loadingAnswers.delete(rid); render(); }
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
      onDecision:(a, approved, note) => {
        if (!ws || ws.readyState !== 1 || pending.has(a.id)) return;
        // Structured questions compose their answer in decide.js; an unanswered one yields ''.
        if (a.kind === 'clarify' && approved && !note) { $('live-notice').textContent = a.detail?.questions?.length ? '모든 질문에 답해 주세요.' : '답을 적어 주세요.'; return; }
        ws.send(JSON.stringify({type:'approval.resolve', id:a.id, approved, note}));
        pending.add(a.id); render();
      }});
    const requests = $('requests'); requests.replaceChildren();
    if (!S.requests.size) text(requests, 'p', '요청 없음');
    for (const q of [...S.requests.values()].reverse()) {
      const row = document.createElement('article'); requests.append(row);
      text(row, 'strong', q.text || q.id);
      text(row, 'p', `${q.status} · ${q.phase}`);
      for (const step of q.plan) text(row, 'p', `${step.id} · ${step.instruction || ''} · ${q.steps[step.id] || 'pending'}`);
      if (q.review?.status === 'review_unparsed') text(row, 'p', '리뷰 판정 실패. PI 확인이 필요해요');
      if (q.error) text(row, 'p', q.error);
      for (const f of q.followups || []) {  // asked from the 2.5D request view; the shared reducer tracks them
        text(row, 'p', `이어 묻기: ${f.text} → ${f.status === 'done' ? f.answer + (f.answer_truncated ? '…' : '') : f.status === 'running' ? '답 기다리는 중' : f.error || '답하지 못함'}`);
        if (f.status === 'done' && f.answer_truncated) {
          const more = text(row, 'button', '전문 보기'); more.type = 'button';
          more.disabled = loadingAnswers.has(q.id); more.onclick = () => loadFullAnswers(q.id);
        }
      }
    }
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
        if (ev.type === 'approval.stale') {
          pending.delete(ev.data?.id); S.approvals.delete(ev.data?.id);
          $('live-notice').textContent = '이미 끝난 승인 요청입니다.';
        }
        for (const effect of apply(ev)) {
          if (effect.type === 'toast') $('live-notice').textContent = effect.text;
          if (effect.type === 'renderAfter') setTimeout(render, effect.ms);
        }
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
