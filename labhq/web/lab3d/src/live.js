import {answerRequired, revisionNoteMissing, syncDecisionCards, syncDecisionHistory, createHistoryVisibility} from '../../ui/decide.js';

// DOM controls and transport stay outside the shared event reducer.
export function startLiveOffice(onState) {
  const {S, apply, selectRequest, visual, stateText, currentTask, fillRequestDetail, KIND_KO, phaseLabel} = globalThis.LabHQState.createOfficeState({
    focusStorage: globalThis.LabHQState.createRequestFocusStorage(globalThis.LABHQ_BOOT)});
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const demo = globalThis.LABHQ_BOOT?.demo === true;
  const tokenKey = globalThis.LabHQState.officeStorageKey(globalThis.LABHQ_BOOT, 'labhq_token');
  const readToken = () => { try { return localStorage.getItem(tokenKey) || ''; } catch { return ''; } };
  const saveToken = value => { try { localStorage.setItem(tokenKey, value); } catch {} };
  let token = params.get('token') || readToken(), lastSeq = 0, ws, retry, backoff = 1000;
  if (params.has('token')) {
    saveToken(token); params.delete('token');
    history.replaceState(null, '', location.pathname + (params.size ? `?${params}` : ''));
  }
  document.body.classList.add('live-mode');
  if (demo) document.body.classList.add('gateway-demo');
  $('live-panel').hidden = false; $('demo-2d').hidden = true;
  document.querySelector('.demo').textContent = demo ? '데모(mock)' : 'LIVE';
  $('state').disabled = true; $('randomize').hidden = true;
  const pending = new Set(), loadingAnswers = new Set();
  const historyVisibility = createHistoryVisibility(`live:${globalThis.LABHQ_BOOT?.instance || ''}`);
  let decisionHistory = [];
  async function refreshHistory() {
    try {
      const response = await fetch('/api/approvals/history?limit=50', {headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) return;
      decisionHistory = await response.json(); render();
    } catch {}
  }
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
  async function resumeLogin(hold) {
    try {
      const response = await fetch(`/api/requests/${encodeURIComponent(hold.request_id)}/steps/${encodeURIComponent(hold.step_id)}/resume-quota`,
        {method:'POST', headers:{Authorization:`Bearer ${token}`, 'Content-Type':'application/json'}, body:'{}'});
      if (!response.ok) throw new Error(String(response.status));
      notice('로그인 대기를 풀었어요.');
    } catch (error) { notice(`재개하지 못했어요: ${error.message}`); }
  }
  function text(parent, tag, value) {
    const el = document.createElement(tag); el.textContent = value; parent.append(el); return el;
  }
  function render() {
    $('connection').textContent = globalThis.LabHQState.connectionLabel(S.conn, demo);
    $('demo-banner').hidden = !demo;
    if (demo) $('demo-banner').textContent = globalThis.LabHQState.demoBannerText(S.demoStatus);
    $('token-form').hidden = S.conn !== 'auth' && S.conn !== 'offline';
    $('approval-count').textContent = S.approvals.size + S.engineHolds.size;
    const holdRows = [...S.engineHolds.values()].map(hold => {
      const row = document.createElement('article');
      text(row, 'strong', `로그인 대기 · ${hold.engine}`);
      text(row, 'pre', hold.command || '');
      const button = text(row, 'button', '로그인했어요 · 다시 시도'); button.type = 'button';
      button.onclick = () => resumeLogin(hold); return row;
    });
    $('login-holds').replaceChildren(...holdRows);
    syncDecisionCards($('approvals'), S.approvals.values(), [], { tagName:'article', listTag:'div', kindLabels:KIND_KO,
      requests:S.requests, emptyText:'대기 승인 없음', disabled:a => S.conn !== 'live' || pending.has(a.id),
      onDecision:(a, approved, note, _type, choice, storage) => {
        if (!ws || ws.readyState !== 1 || pending.has(a.id)) return;
        // Structured questions compose their answer in decide.js; an unanswered one yields ''.
        if (a.kind === 'clarify' && approved && !note) { notice(a.detail?.questions?.length ? '모든 질문에 답해 주세요.' : '답을 적어 주세요.'); return; }
        if (a.kind === 'question' && approved && !note && answerRequired(a)) { notice('선택지를 하나 고르거나 답을 적어 주세요.'); return; }
        // CP2 수정 요청 re-plans from its note through a new CP1, so the note says what to fix (R10).
        const missing = revisionNoteMissing(a, choice, note);
        if (missing) { notice(missing); return; }
        // CP2 evidence review carries its approve/revise/deny choice; the note never decides it (#90).
        const decision = choice ? {type:'approval.resolve', id:a.id, approved, note, choice}
          : {type:'approval.resolve', id:a.id, approved, note};
        if (storage) decision.storage = storage;
        ws.send(JSON.stringify(decision));
        pending.add(a.id); render();
      }});
    if ($('approval-history')) syncDecisionHistory($('approval-history'), decisionHistory, {
      kindLabels: KIND_KO, requests: S.requests, visibility: historyVisibility,
      onOpenRequest: id => { selectRequest(id); render(); const row = [...$('requests').children].find(row => row.dataset.requestId === id); row?.scrollIntoView({block:'start'}); },
    });
    const requests = $('requests'), oldRows = new Map([...requests.children]
      .filter(row => row.dataset?.requestId).map(row => [row.dataset.requestId, row]));
    const rows = [];
    if (!S.requests.size) text({append: row => rows.push(row)}, 'p', '요청 없음');
    for (const q of globalThis.LabHQState.sortRequests(S.requests.values())) {
      const status = globalThis.LabHQState.requestStatusText(q, S.approvals);  // R14: never a raw status
      const key = JSON.stringify([q.text, q.status, status, q.phase, q.plan, q.steps, q.processing, q.review, q.error, q.report,
        q.report_appendix, q.report_truncated, q.report_appendix_truncated, q.followups, q.piNotes,
        q.assumptions, q.bundlePath, q.bundleStatus, q.bundleGrade, q.bundleGradeReasons, q.bundleWarning, loadingAnswers.has(q.id)]);
      const prior = oldRows.get(q.id);
      if (prior?.dataset.renderKey === key) { rows.push(prior); continue; }
      const row = document.createElement('article'); row.dataset.requestId = q.id; row.dataset.renderKey = key; rows.push(row);
      text(row, 'strong', q.text || q.id);
      // Same labels as the 2.5D board. A finished request says 완료/실패 in its status already, so no phase then.
      text(row, 'p', [status, q.phase === 'done' ? '' : phaseLabel(q), globalThis.LabHQState.processingLabel(q)]
        .filter(Boolean).join(' · '));
      const audit = text(row, 'a', '감사 번들');
      audit.href = `/api/requests/${encodeURIComponent(q.id)}/audit-bundle`;
      audit.onclick = event => { event.preventDefault(); downloadAuditBundle(q.id); };
      // The same plan assumptions and request bundle the 2.5D view shows (PR #387, #389 follow-ups).
      if ((q.assumptions || []).length) {
        text(row, 'h4', '가정');
        for (const value of q.assumptions) text(row, 'p', `· ${value}`);
        text(row, 'p', '바꾸려면 실행 중 메모로 알려 주세요.');
      }
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
      if (q.bundlePath) {
        const reasons = (q.bundleGradeReasons || []).join('; ');
        const grade = q.bundleGrade ? ` · 재현 등급 ${q.bundleGrade}${reasons ? ` (${reasons})` : ''}` : '';
        text(row, 'p', q.bundleStatus === 'incomplete'
          ? `요청 묶음(불완전): ${q.bundlePath} · 빠진 파일은 실행 기록과 MANIFEST.tsv에 있어요${grade}`
          : `요청 묶음: ${q.bundlePath}${grade}`);
      }
      if (q.bundleWarning) {
        text(row, 'p', `요청 묶음 경고: ${q.bundleWarning}`);
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
    onState(S, visual, stateText, currentTask);
  }
  function open() {
    clearTimeout(retry);
    if (ws) { ws.onclose = null; ws.onmessage = null; ws.close(); }
    if (!token) { S.conn = 'auth'; render(); return; }
    S.conn = 'connecting'; render();
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/client?token=${encodeURIComponent(token)}${lastSeq ? `&since=${lastSeq}` : ''}`);
    ws.onopen = () => { S.conn = 'live'; backoff = 1000; refreshHistory(); render(); };
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
        if (ev.type === 'approval.resolved') refreshHistory();
        render();
      } catch (error) { console.warn('bad event', error); }
    };
    ws.onclose = event => {
      const action = globalThis.LabHQState.websocketCloseAction(event.code);
      pending.clear(); S.conn = action.connection; render();
      if (action.retry) { retry = setTimeout(open, backoff); backoff = Math.min(15000, backoff * 2); }
    };
  }
  $('token-form').addEventListener('submit', event => {
    event.preventDefault(); token = $('token').value.trim(); saveToken(token); lastSeq = 0; open();
  });
  setInterval(() => { if (S.approvals.size || S.activeTasks.size) render(); }, 1000);
  open();
}
