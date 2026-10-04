// Shared event state, with no DOM, transport, or timers. Classic script also runs in Node.
(function (root) {
'use strict';
const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const isContract = a => !!a && (a.employment === 'contract' || String(a.id).startsWith('c_'));
const ACTIVE_REQUEST_STATES = new Set(['running', 'waiting_for_runner', 'waiting_quota']);
const TERMINAL_REQUEST_STATES = new Set(['done', 'failed', 'cancelled', 'rejected']);
const isActiveRequest = status => ACTIVE_REQUEST_STATES.has(status);
const isTerminalRequest = status => TERMINAL_REQUEST_STATES.has(status);
// Cost text (#270): confirmed, price-table estimate and unaccounted tasks stay apart; an unknown is never $0.
const usd = v => `$${(Number(v) || 0).toFixed(2)}`;
function costParts(actual, estimated, unknown) {
  const parts = [];
  if (actual > 0 || !(estimated > 0 || unknown)) parts.push(`확인 ${usd(actual)}`);
  if (estimated > 0) parts.push(`추정 ${usd(estimated)}`);
  if (unknown) parts.push(`미집계 ${unknown}건`);
  return parts.join(' + ');
}
const summaryParts = s => costParts(Number(s.actual_usd) || 0, Number(s.estimated_usd) || 0, Number(s.unknown_count) || 0);
function costLabel(q) {
  if (q && q.costSummary) return summaryParts(q.costSummary);
  const cost = Number(q && q.cost) || 0;
  return q && q.costKnown === false ? `${cost ? usd(cost) + ' + ' : ''}비용 미집계` : usd(cost);
}
function engineCostLabel(summary) {
  if (!summary || !summary.by_engine) return '';
  const engines = Object.entries(summary.by_engine).map(([name, e]) => `${name} ${summaryParts(e)}`).join(' · ');
  const stale = (summary.prices || []).filter(p => p && p.stale).map(p => `${p.engine} ${p.model} (${p.checked_on} 확인)`);
  return [engines, Number(summary.estimated_usd) > 0 ? '추정은 API 가격표 환산이며 청구액이 아닙니다' : '',
    stale.length ? `가격표 오래됨: ${stale.join(', ')}` : ''].filter(Boolean).join('; ');
}
function totalCostLabel(requests, total) {
  let estimated = 0, unknown = 0, legacyUnknown = false;
  for (const q of requests) {
    if (q.costSummary) { estimated += Number(q.costSummary.estimated_usd) || 0; unknown += Number(q.costSummary.unknown_count) || 0; }
    else if (q.costKnown === false) legacyUnknown = true;
  }
  return `사용 비용 ${usd(total)}${estimated > 0 ? ` (추정 ${usd(estimated)} 포함)` : ''}` +
    (unknown ? ` + 미집계 ${unknown}건${legacyUnknown ? ' 이상' : ''}` : legacyUnknown ? ' + 비용 미집계' : '');
}
function applyCostSummary(q, summary) {
  if (!summary || typeof summary !== 'object') return;
  q.costSummary = summary; q.costKnown = !(Number(summary.unknown_count) > 0);
}
function createOfficeState({mode = 'live', now = () => Date.now() / 1000} = {}) {
const S = {
  agents: new Map(), approvals: new Map(), suggestions: [], requests: new Map(), current: null,
  jobs: new Map(), feed: [], cost: 0, conn: mode === 'demo' ? 'demo' : 'connecting', projects: [],
  lastSay: {}, taskStep: new Map(), seq: 0, doorUntil: 0,
};
// Task detail is derived from events and may be rebuilt from a snapshot.
// Keep it out of legacy state serialization while exposing it to both UIs.
Object.defineProperty(S, 'stepDetails', { value: new Map(), enumerable: false });
Object.defineProperty(S, 'askDetails', { value: new Map(), enumerable: false });
// #36 additions stay non-enumerable too, so the legacy state digests (tests/web_state.cjs) are unchanged.
Object.defineProperty(S, 'defaultRefs', { value: [], writable: true, enumerable: false });
function resetSnapshotState() {
  S.agents.clear(); S.approvals.clear(); S.suggestions = []; S.requests.clear(); S.current = null;
  S.jobs.clear(); S.feed = []; S.cost = 0; S.projects = [];
  S.lastSay = {}; S.taskStep.clear(); S.stepDetails.clear(); S.askDetails.clear(); S.seq = 0; S.doorUntil = 0;
  S.defaultRefs = [];
}
const STATE_KO = { idle: '쉬는 중', queued: '순서 기다림', working: '작업 중', waiting: '승인 기다림',
  hibernating: 'HPC 기다리는 중', done: '완료', error: '문제 발생' };
const KIND_KO = { hpc_submit: 'HPC 제출', tool_permission: '도구 권한', budget: '예산 초과', recruit: '채용', download: '대용량 다운로드', clarify: 'PI 질문', scope: '범위 확인', research_evidence: 'CP2 증거 검토', codex_sandbox_setup: 'Codex sandbox 준비' };
const JOB_KO = { queued: '대기', running: '실행 중', completed: '완료', failed: '실패', held: '보류', suspended: '일시정지',
  cancelled: '취소', unknown_finished: '종료 (확인 필요)', error: '오류', missing: '확인 중' };
const GH_KO = { issue: 'GitHub에 이 요청의 이슈를 열었어요', plan: '이슈에 계획을 올렸어요', review: '이슈에 리뷰 결과를 올렸어요',
  report: '보고서를 저장소에 커밋했어요', final: '이슈에 최종 보고를 올리고 닫았어요', recruit: '이슈에 파견직 합류를 기록했어요',
  pr: 'PR을 열었어요', codex_review: 'PR에 "@codex review" 코멘트를 남겼어요' };
const PHASES = [['briefing', '브리핑'], ['plan', '계획'], ['execute', '실행'], ['review', '리뷰'], ['report', '보고']];

const ag = id => S.agents.get(id);
const visual = a => a.state === 'done' && now() - (a.stateAt || 0) > 3
  ? 'idle' : STATE_KO[a.state] ? a.state : 'idle';
const nick = id => { const a = ag(id); return a ? String(a.name || a.id).split(' ')[0] : ({ pi: '나', hpc: 'HPC', github: 'GitHub', system: '시스템' }[id] || id || ''); };
function upsertAgent(a) {
  const prev = S.agents.get(a.id) || { state: 'idle', task: '', say: '', tool: '', log: [] };
  const next = { ...prev, ...a }, usage = next.usage || {}, toolCalls = next.toolCalls || 0;
  delete next.usage; delete next.toolCalls;
  Object.defineProperty(next, 'usage', { value: usage, writable: true, enumerable: false });
  Object.defineProperty(next, 'toolCalls', { value: toolCalls, writable: true, enumerable: false });
  S.agents.set(a.id, next);
}
function req(rid) {
  if (!S.requests.has(rid)) {
    const q = { id: rid, text: '', steps: {}, plan: [], github: [], cost: 0, costKnown: true, phase: 'briefing', status: 'running', created_at: now() };
    Object.defineProperty(q, 'references', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'followups', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'piNotes', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'costSummary', { value: null, writable: true, enumerable: false });  // #270
    Object.defineProperty(q, 'pipelinePr', { value: null, writable: true, enumerable: false });
    S.requests.set(rid, q);
  }
  return S.requests.get(rid);
}
function stepDetail(rid, sid) {
  const key = `${rid}:${sid}`;
  if (!S.stepDetails.has(key)) S.stepDetails.set(key, { attempts: 0, outputs: [], missing_outputs: [], review_issues: [] });
  return S.stepDetails.get(key);
}
function setPlan(q, plan) {
  q.plan = (plan.steps || []).map(s => ({ ...s, depends_on: s.depends_on || [] }));
  for (const s of q.plan) if (!q.steps[s.id]) q.steps[s.id] = 'pending';
}
function logTo(a, text, ts) { a.log.push({ ts, text: short(text, 240) }); if (a.log.length > 40) a.log.shift(); }
function feed(item, ts, rid) { S.feed.unshift({ ...item, ts: ts || now(), rid }); if (S.feed.length > 120) S.feed.length = 120; }
function stripPrompt(p) {
  const m = /Your step \(([^)]+)\):\s*([\s\S]*?)(?:\n\n|$)/.exec(p || '');
  return m ? `${m[1]}: ${m[2]}` : (p || '');
}
// #184: every way an approval ends (승인·거절·timeout, gateway restart, decided elsewhere) drops it and
// tells the page to clear the notice it raised for that approval.
function endApproval(id, effects) {
  S.approvals.delete(id);
  effects.push({ type: 'toast.clear', approval_id: id });
}
// The gateway sends no request status event for quota: the request waits exactly while one of its steps does.
function syncQuotaStatus(q) {
  const parked = Object.values(q.steps).includes('waiting_quota');
  if (parked && !isTerminalRequest(q.status)) q.status = 'waiting_quota';
  else if (!parked && q.status === 'waiting_quota') q.status = 'running';
}
function pickCurrent() {
  const all = [...S.requests.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  S.current = (all.find(r => isActiveRequest(r.status)) || all[0] || {}).id || null;
}
function syncRoster(list, replay) {
  const ids = new Set(list.map(a => a.id));
  for (const a of list) {
    const isNew = !S.agents.has(a.id);
    upsertAgent(a);
    if (isNew && !replay && S.agents.size > 1) { S.agents.get(a.id).arriving = now(); S.doorUntil = now() + 2.2; }
  }
  for (const id of [...S.agents.keys()]) if (!ids.has(id)) S.agents.delete(id);
}
function onDispatch(ev) {
  const d = ev.data || {}, id = ev.agent_id, rid = ev.request_id, q = rid ? req(rid) : null, a = ag(id);
  if (a) a.task = d.title || short(stripPrompt(d.prompt), 60);
  if (d.step_id) S.taskStep.set(ev.task_id, d.step_id);
  if (q) {
    const ph = { briefing: 'briefing', plan: 'plan', review: 'review', synthesis: 'report', direct: 'execute' }[d.kind];
    if (ph) q.phase = ph;
    if (d.step_id) q.steps[d.step_id] = 'working';
  }
  const wake = /HPC 결과/.test(d.title || '');
  let m = null;
  if (d.kind === 'briefing') m = { who: 'cso', to: id, text: '착수 브리핑 부탁해요' };
  else if (d.kind === 'review') m = { who: 'cso', to: id, text: '결과 검토 부탁해요' };
  else if (d.kind === 'direct') m = { who: 'pi', to: id, text: short(d.title || d.prompt, 110) };
  else if (wake) m = { who: 'hpc', to: id, text: '작업이 끝났어요. 이어서 해 주세요' };
  else if (d.kind === 'step' || !d.kind) m = { who: 'cso', to: id, text: short(d.title || stripPrompt(d.prompt), 110) };
  if (m) feed(m, ev.ts, rid);
}
function setPipelinePr(q, d) {
  q.pipelinePr = { ...d };
  if (d.status === 'open' && d.url && !q.github.some(g => g.kind === 'pipeline_pr' && g.url === d.url))
    q.github.push({ kind: 'pipeline_pr', url: d.url, number: d.number });
}
function apply(ev, replay = false) {
  const effects = [];
  const t = ev.type, d = ev.data || {}, id = ev.agent_id, rid = ev.request_id, ts = ev.ts || now();
  switch (t) {
    case 'snapshot': {
      const pendingBefore = [...S.approvals.keys()];
      resetSnapshotState();
      (d.agents || []).forEach(upsertAgent);
      S.projects = d.projects || [];
      S.defaultRefs = d.default_references || [];
      (d.recent_events || []).forEach(e => apply(e, true));
      for (const r of d.requests || []) {
        const q = req(r.id);
        Object.assign(q, { text: r.text, status: r.status, mode: r.mode, project_id: r.project_id, created_at: r.created_at, cost: r.cost_usd || 0, costKnown: r.cost_known !== false });
        if (typeof r.report === 'string') q.report = r.report;
        if (typeof r.report_appendix === 'string') q.report_appendix = r.report_appendix;
        if (r.report_truncated) Object.assign(q, { report_truncated: true, report_chars: r.report_chars });
        if (r.report_appendix_truncated) Object.assign(q, { report_appendix_truncated: true,
          report_appendix_chars: r.report_appendix_chars });
        q.costSummary = null; applyCostSummary(q, r.cost_summary);
        if (r.plan) setPlan(q, r.plan);
        q.references = r.references || [];
        q.followups = (r.followups || []).map(f => ({ ...f }));
        q.piNotes = (r.pi_notes || []).map(note => ({ ...note }));
        Object.assign(q.steps, r.step_status || {});
        for (const [sid, detail] of Object.entries(r.step_details || {})) Object.assign(stepDetail(r.id, sid), detail);
        if (r.review) q.review = r.review;
        if (r.pipeline_pr) setPipelinePr(q, r.pipeline_pr);  // the stored status outlives the replayed events
        if (!isActiveRequest(r.status)) q.phase = 'done';
      }
      const restored = new Map();
      for (const task of d.running_tasks || []) {
        if (!task.request_id || !task.step_id) continue;
        const key = `${task.request_id}:${task.step_id}`, previous = restored.get(key);
        const rank = [task.state === 'running' ? 1 : 0, Number(task.dispatched_at) || 0];
        if (previous && (previous.rank[0] > rank[0] ||
            (previous.rank[0] === rank[0] && previous.rank[1] > rank[1]))) continue;
        restored.set(key, { task, rank });
      }
      for (const { task } of restored.values()) {
        const q = S.requests.get(task.request_id);
        if (!q) continue;
        q.steps[task.step_id] = task.state === 'running' ? 'working' : task.state;
        Object.assign(stepDetail(task.request_id, task.step_id), { task_id: task.id });
      }
      S.cost = (d.requests || []).reduce((total, r) => total + (Number(r.cost_usd) || 0), 0);
      S.approvals.clear();
      (d.approvals || []).forEach(x => S.approvals.set(x.id, x));
      for (const id of pendingBefore) if (!S.approvals.has(id)) effects.push({ type: 'toast.clear', approval_id: id });
      for (const a of S.agents.values()) if (a.state === 'done' || a.state === 'error' || a.state === 'queued') a.state = 'idle';
      pickCurrent();
      break;
    }
    case 'roster.updated': syncRoster(d.agents || [], replay); break;
    case 'runner.online': feed({ who: 'system', text: `러너 연결됨 (${d.runner_id})` }, ts); break;
    case 'runner.offline': feed({ who: 'system', text: `러너 연결 끊김 (${d.runner_id})`, cls: 'alert' }, ts); break;
    case 'agent.status': {
      const a = ag(id); if (!a) break;
      a.state = d.state || a.state; a.stateAt = ts;
      if (d.task) a.task = d.task;
      if (d.state === 'done' || d.state === 'idle') a.tool = '';
      if (d.error) { a.error = d.error; logTo(a, `오류: ${d.error}`, ts); }
      if (d.state === 'done' && !replay) effects.push({ type: 'renderAfter', ms: 3200 });
      break;
    }
    case 'agent.tool': { const a = ag(id); if (!a) break; a.tool = d.name; a.toolAt = ts; a.toolCalls = (a.toolCalls || 0) + 1; logTo(a, `도구 ${toolLabel(d.name)} ${short(d.input, 80)}`, ts); break; }
    case 'agent.tool_error': { const a = ag(id); if (a) logTo(a, `도구 오류: ${d.text}`, ts); break; }
    case 'agent.log': {
      if (d.level === 'debug' || d.level === 'thinking') break;
      const a = ag(id); if (!a || !d.text) break;
      a.say = d.text; a.sayAt = ts; logTo(a, d.text, ts);
      // An alert (a read-only run changed files) always reaches the feed, never throttled with ordinary talk.
      if (d.level === 'alert') { feed({ who: id, text: short(d.text, 300), cls: 'alert' }, ts, rid); break; }
      if (!S.lastSay[id] || ts - S.lastSay[id] > 6) { S.lastSay[id] = ts; feed({ who: id, text: short(d.text, 150) }, ts, rid); }
      break;
    }
    case 'agent.ask': {
      S.askDetails.set(d.id, { ...d, agent_id: d.agent_id || id });
      if (S.askDetails.size > 120) S.askDetails.delete(S.askDetails.keys().next().value);
      feed({ who: d.agent_id || id, to: String(d.to || '').replace(/^colleague:/, ''),
        text: `질문: ${short(d.question, 150)}` }, ts, rid);
      break;
    }
    case 'agent.answer': {
      const ask = S.askDetails.get(d.ask_id) || {};
      const rejected = d.status === 'rejected';
      const question = d.question || ask.question;
      feed({ who: d.from || id, to: d.to || ask.agent_id,
        text: `${rejected ? '거절' : '답변'}: ${short(rejected ? d.reason : d.answer, 130)}${question ? ` (질문: ${short(question, 70)})` : ''}`,
        cls: rejected ? 'alert' : '' }, ts, rid);
      break;
    }
    case 'agent.usage': {
      const c = Number(d.cost_usd) || 0;
      const a = ag(id);
      if (a) a.usage = { ...(a.usage || {}), ...(d.tokens || {}), ...('num_turns' in d ? { num_turns: d.num_turns } : {}) };
      if (c > 0) { S.cost += c; if (rid) req(rid).cost += c; }
      if (rid && d.cost_known === false) req(rid).costKnown = false;
      // A live amount the stored summary does not hold yet: show the running total until the next summary (#270).
      if (rid && (c > 0 || d.cost_known === false)) req(rid).costSummary = null;
      break;
    }
    case 'task.dispatched': {
      onDispatch(ev);
      if (rid && d.step_id) Object.assign(stepDetail(rid, d.step_id), {
        task_id: ev.task_id, attempts: Math.max(stepDetail(rid, d.step_id).attempts || 0, Number(d.attempt) || 1),
      });
      break;
    }
    case 'task.result': {
      const sid = S.taskStep.get(ev.task_id);
      if (sid && rid) {
        if (d.ok === false) req(rid).steps[sid] = 'error';
        Object.assign(stepDetail(rid, sid), { task_id: ev.task_id, ok: d.ok, text: d.text || '', error: d.error || '',
          outputs: d.outputs || [], missing_outputs: d.missing_outputs || [] });
      }
      break;
    }
    case 'approval.requested': {
      S.approvals.set(d.id, { ...d, agent_id: d.agent_id || id, request_id: d.request_id || rid });
      feed({ who: d.agent_id || id || 'cso', text: `승인 요청: ${short(d.summary, 130)}`, cls: 'alert' }, ts, rid);
      if (!replay) effects.push({ type: 'toast', text: `승인 요청이 왔어요: ${short(d.summary, 50)}`, approval_id: d.id });
      break;
    }
    case 'approval.expired': case 'approval.stale': endApproval(d.id, effects); break;
    case 'approval.resolved': endApproval(d.id, effects); feed({ who: 'pi', text: d.approved ? '승인했어요' : `${d.choice === 'revise' ? '수정을 요청했어요' : '거절했어요'}${d.note ? ` (${short(d.note, 60)})` : ''}` }, ts, rid); break;
    case 'job.submitted': S.jobs.set(String(d.job_id), { id: String(d.job_id), name: d.name, state: 'queued', agent: id, ts }); feed({ who: id, text: `HPC 작업 제출: ${d.name || ''} (${d.job_id})` }, ts, rid); break;
    case 'job.state': {
      const j = S.jobs.get(String(d.job_id)) || { id: String(d.job_id), name: d.name, agent: id };
      Object.assign(j, { state: d.state, exit: d.exit_status, ts }); S.jobs.set(j.id, j);
      break;
    }
    case 'jobs.finished': feed({ who: 'hpc', text: `작업이 끝나서 ${nick(id)}를 깨웠어요` }, ts, rid); break;
    case 'request.created': {
      const q = req(rid);
      Object.assign(q, { text: d.text, mode: d.mode, project_id: d.project_id, status: 'running', phase: d.mode === 'direct' ? 'execute' : 'briefing', created_at: ts });
      q.references = d.references || [];
      S.current = rid;
      feed({ who: 'pi', text: `새 요청: ${short(d.text, 130)}` }, ts, rid);
      break;
    }
    case 'request.plan': {
      const q = req(rid); setPlan(q, d); q.phase = 'execute';
      feed({ who: 'cso', text: `계획을 세웠어요: ${(d.steps || []).length}단계${(d.recruit || []).length ? ', 파견직 채용 제안 1건' : ''}` }, ts, rid);
      break;
    }
    case 'request.step_attempt': {
      if (rid && d.step_id) stepDetail(rid, d.step_id).attempts = Math.max(stepDetail(rid, d.step_id).attempts || 0, Number(d.attempt) || 1);
      break;
    }
    case 'request.step_quota_wait': {
      const q = req(rid); q.steps[d.step_id] = 'waiting_quota';
      Object.assign(stepDetail(rid, d.step_id), { quota_resume_at: d.resume_at, quota_engine: d.engine });
      syncQuotaStatus(q);
      break;
    }
    case 'request.step_quota_resumed': {
      const q = req(rid), detail = stepDetail(rid, d.step_id);
      if (q.steps[d.step_id] === 'waiting_quota') q.steps[d.step_id] = 'pending';
      delete detail.quota_resume_at; delete detail.quota_engine;
      syncQuotaStatus(q);
      break;
    }
    case 'request.questions': feed({ who: 'cso', text: `확인이 필요해요: ${short((d.questions || []).join(' / '), 150)}`, cls: 'alert' }, ts, rid); break;
    case 'request.step_done': { const q = req(rid), detail = stepDetail(rid, d.step_id); q.steps[d.step_id] = d.ok === false ? 'error' : 'done'; Object.assign(detail, { attempts: d.attempts || detail.attempts, error: d.reason || detail.error }); delete detail.quota_resume_at; delete detail.quota_engine; syncQuotaStatus(q); break; }
    case 'request.step_skipped': { const q = req(rid); q.steps[d.step_id] = 'skipped'; stepDetail(rid, d.step_id).error = d.reason || ''; syncQuotaStatus(q); break; }
    case 'request.review': {
      const q = req(rid), sc = d.scores || {};
      q.review = d; q.phase = d.verdict === 'revise' ? 'execute' : 'review';
      for (const step of q.plan) stepDetail(rid, step.id).review_issues = (d.issues || []).filter(issue => issue.step_id === step.id);
      if (d.verdict === 'revise') (d.issues || []).forEach(i => { if (i.step_id in q.steps) q.steps[i.step_id] = 'revise'; });
      const unparsed = d.status === 'review_unparsed';
      feed({ who: 'sci_reviewer', text: unparsed ? '리뷰 판정 실패. PI 확인이 필요해요'
        : d.verdict === 'revise' ? `수정 요청: ${short(((d.issues || [])[0] || {}).problem || '', 90)}`
        : d.verdict === 'accept' ? `통과. 질문 부합 ${sc.addresses_question}/5, 근거 ${sc.evidence}/5, 철저성 ${sc.thoroughness}/5`
        : '리뷰 판정 실패. PI 확인이 필요해요', cls: unparsed ? 'alert' : '' }, ts, rid);
      break;
    }
    case 'recruit.suggested': {
      const key = d.repo || d.paper;
      const hired = [...S.agents.values()].some(a => isContract(a) && key && String(key).toLowerCase().includes(String(a.id).slice(2)));
      if (!hired && !S.suggestions.some(s => (s.repo || s.paper) === key)) S.suggestions.push({ ...d, request_id: rid, id: `sug_${++S.seq}` });
      feed({ who: 'cso', text: `파견직 채용을 제안해요: ${short(key, 70)}` }, ts, rid);
      break;
    }
    case 'recruit.status': {
      const a = ag('recruiter');
      if (a && d.slug) a.task = `${d.slug} ${d.stage === 'converting' ? '변환 중' : d.stage === 'probation' ? '수습 평가 중' : d.stage || ''}`;
      feed({ who: 'recruiter', text: d.stage === 'converting' ? `${d.slug} 논문을 에이전트로 바꾸는 중이에요` : `${d.slug} 수습 평가 중이에요` }, ts, rid);
      break;
    }
    case 'recruit.done': {
      const na = d.agent || {};
      if (na.id) { const isNew = !S.agents.has(na.id); upsertAgent(na); if (isNew && !replay) { ag(na.id).arriving = now(); S.doorUntil = now() + 2.2; } }
      S.suggestions = S.suggestions.filter(s => !String(s.repo || s.paper || '').toLowerCase().includes(String(na.id || '').slice(2)));
      feed({ who: 'recruiter', text: `${na.name || na.id} 입사했어요 (수습 ${d.passed_probation ? '통과' : '진행 중'})` }, ts, rid);
      break;
    }
    case 'recruit.failed': feed({ who: 'recruiter', text: `채용하지 못했어요: ${short(d.error, 120)}`, cls: 'alert' }, ts, rid); break;
    case 'request.completed': case 'request.failed': {
      const q = req(rid);
      q.status = t === 'request.completed' && d.ok !== false ? 'done' : 'failed'; q.phase = 'done';
      if (typeof d.cost_usd === 'number' && Number.isFinite(d.cost_usd) && d.cost_usd >= 0) {
        S.cost += d.cost_usd - q.cost; q.cost = d.cost_usd;
      }
      if (d.cost_known === false) q.costKnown = false;
      applyCostSummary(q, d.cost_summary);
      if (d.report) q.report = d.report;
      if (d.report_appendix) q.report_appendix = d.report_appendix;
      delete q.report_truncated; delete q.report_chars;
      delete q.report_appendix_truncated; delete q.report_appendix_chars;
      if (d.report_truncated) Object.assign(q, { report_truncated: true, report_chars: d.report_chars });
      if (d.report_appendix_truncated) Object.assign(q, { report_appendix_truncated: true,
        report_appendix_chars: d.report_appendix_chars });
      if (d.error) q.error = d.error;
      feed({ who: 'cso', text: q.status === 'done' ? '최종 보고서를 올렸어요' : `요청이 실패했어요: ${short(d.error, 100)}`, cls: q.status === 'done' ? '' : 'alert' }, ts, rid);
      break;
    }
    case 'request.followup': {
      const q = req(rid), entry = { id: d.id, text: d.text, agent_id: d.agent_id, status: 'running', asked_at: ts };
      q.followups = [...q.followups.filter(f => f.id !== d.id), entry];
      feed({ who: 'pi', to: d.agent_id || 'cso', text: `이어 묻기: ${short(d.text, 130)}` }, ts, rid);
      break;
    }
    case 'request.note': {
      const q = req(rid), entry = { id: d.id, text: d.text, at: d.at || ts };
      q.piNotes = [...q.piNotes.filter(note => note.id !== entry.id), entry];
      feed({ who: 'pi', to: 'cso', text: `실행 중 메모: ${short(d.text, 130)}` }, ts, rid);
      break;
    }
    case 'request.followup_done': {
      const q = req(rid), prev = q.followups.find(f => f.id === d.id);
      const entry = { ...(prev || { id: d.id, text: '' }), status: d.ok ? 'done' : 'failed', answer: d.answer || '', error: d.error || '' };
      delete entry.answer_truncated; delete entry.answer_chars;
      if (d.answer_truncated) Object.assign(entry, { answer_truncated: true, answer_chars: d.answer_chars });  // a snapshot replay (#126)
      q.followups = prev ? q.followups.map(f => (f.id === d.id ? entry : f)) : [...q.followups, entry];
      if (typeof d.cost_usd === 'number' && Number.isFinite(d.cost_usd) && d.cost_usd >= 0) { S.cost += d.cost_usd - q.cost; q.cost = d.cost_usd; }
      if (d.cost_known === false) q.costKnown = false;
      applyCostSummary(q, d.cost_summary);
      feed({ who: entry.agent_id || 'cso', to: 'pi', text: d.ok ? `답변: ${short(d.answer, 130)}` : `이어 묻기에 답하지 못했어요: ${short(d.error, 100)}`,
        cls: d.ok ? '' : 'alert' }, ts, rid);
      break;
    }
    case 'github.posted': { const q = req(rid); q.github.push({ kind: d.kind, url: d.url, number: d.number }); feed({ who: 'github', text: GH_KO[d.kind] || 'GitHub에 업데이트했어요', url: d.url }, ts, rid); break; }
    case 'github.failed': feed({ who: 'github', text: `GitHub 업데이트 실패: ${short(d.error, 110)}`, cls: 'alert' }, ts, rid); break;
    case 'pipeline.pr': {
      setPipelinePr(req(rid), d);
      feed({ who: 'github', text: d.status === 'open' ? `새 pipeline PR을 열었어요: ${d.name}`
        : d.status === 'pending' ? `새 pipeline PR 대기: ${short(d.reason, 100)}`
        : `새 pipeline PR 거부: ${short(d.reason, 100)}`,
        cls: d.status === 'open' ? '' : 'alert', url: d.url || undefined }, ts, rid);
      break;
    }
    default: break;
  }
  return effects;
}
// #126: a snapshot carries only the head of a long follow-up answer. The full request
// (GET /api/requests/{id}, fetched by the page) fills the answers in; returns how many it filled.
function fillFollowups(rid, request) {
  const q = S.requests.get(rid), full = new Map(((request && request.followups) || []).map(f => [f && f.id, f]));
  if (!q) return 0;
  let filled = 0;
  q.followups = q.followups.map(f => {
    const whole = full.get(f.id);
    if (!f.answer_truncated || !whole || typeof whole.answer !== 'string') return f;
    filled++;
    const { answer_truncated, answer_chars, ...rest } = f;
    return { ...rest, answer: whole.answer };
  });
  return filled;
}
function fillRequestDetail(rid, request) {
  const q = S.requests.get(rid);
  let filled = fillFollowups(rid, request);
  if (q?.report_truncated && typeof request?.report === 'string') {
    q.report = request.report;
    delete q.report_truncated; delete q.report_chars;
    filled++;
  }
  if (q && typeof request?.report_appendix === 'string' &&
      (q.report_appendix_truncated || q.report_appendix !== request.report_appendix)) {
    q.report_appendix = request.report_appendix;
    delete q.report_appendix_truncated; delete q.report_appendix_chars;
    filled++;
  }
  return filled;
}
function toolLabel(name) {
  const n = String(name || '').split('__').pop();
  return ({ hpc_submit: 'HPC 제출', hpc_status: 'HPC 확인', WebSearch: '웹 검색', WebFetch: '문헌 읽기', google_web_search: '웹 검색',
    Read: '파일 읽기', Glob: '파일 찾기', Grep: '코드 검색', Bash: '명령 실행', shell: '명령 실행', Edit: '파일 수정', Write: '파일 작성',
    Skill: '스킬 실행', Agent: '서브에이전트', edit: '파일 수정', web_search: '웹 검색' }[n]) || n;
}

return { S, apply, ag, visual, nick, req, setPlan, feed, fillFollowups, fillRequestDetail, toolLabel, STATE_KO, KIND_KO, JOB_KO, PHASES };
}
root.LabHQState = { createOfficeState, costLabel, engineCostLabel, totalCostLabel,
  isActiveRequest, isTerminalRequest };
})(globalThis);
