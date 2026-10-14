// Shared event state, with no DOM, transport, or timers. Classic script also runs in Node.
(function (root) {
'use strict';
const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
function officeStorageKey(boot, key) {
  return boot?.demo === true ? `labhq_demo:${key}` : key;
}
// Storage belongs to the browser origin, demo/live mode and gateway instance; never store request text.
function createRequestFocusStorage(boot, storage = () => root.localStorage) {
  const key = officeStorageKey(boot, `labhq_current_request:${boot?.instance || ''}`);
  return {
    get() { try { return JSON.parse(storage().getItem(key) || 'null'); } catch { return null; } },
    set(value) { try { storage().setItem(key, JSON.stringify(value)); } catch {} },
  };
}
function connectionLabel(conn, demo = false) {
  return {live: demo ? '데모(mock)' : '실시간', demo: '데모(mock)', connecting: '연결 중',
    offline: '연결 끊김 · 재시도 중', auth: '토큰을 확인해 주세요'}[conn] || '';
}
function demoBannerText(status) {
  const base = '데모입니다 — 실제 요청은 labhq up으로 띄운 사무실에서';
  const progress = status?.phase === 'waiting' ? `다음 데모 요청까지 ${status.remaining_s}초`
    : status?.phase === 'running' ? 'mock 요청 진행 중' : 'mock 데모 준비 중';
  return `${base} · ${progress}`;
}
const STAFF_NAMES = {cso:'CSO(부엉이)', chief_of_staff:'비서실장', biologist:'생물학자', data_steward:'데이터 관리자', lit_scout:'문헌 조사원', analyst:'분석가', engineer:'엔지니어', qc_reviewer:'품질 리뷰어', sci_reviewer:'과학 리뷰어', recruiter:'채용 담당', facilities:'시설팀', colleague:'동료 직원', 'bioinfo-agent':'생물정보 직원', pi:'나', hpc:'HPC', github:'GitHub', system:'시스템'};
const STAFF_CHARACTERS = {cso:'owl', chief_of_staff:'penguin', biologist:'bear', data_steward:'squirrel', lit_scout:'fox', analyst:'raccoon', engineer:'octopus', qc_reviewer:'hedgehog', sci_reviewer:'turtle', recruiter:'beaver', 'bioinfo-agent':'otter'};
function staffCharacter(a) {
  const id = String(a?.id || '').toLowerCase();
  return STAFF_CHARACTERS[id] || (id.startsWith('c_') ? 'chick' : a?.character && a.character !== 'paper-hat-chick' && a.character !== 'chick' ? a.character : 'neutral');
}
const isContract = a => !!a && (a.employment === 'contract' || String(a.id).startsWith('c_'));
const ACTIVE_REQUEST_STATES = new Set(['running', 'waiting_for_runner', 'waiting_quota', 'waiting_login', 'waiting_facilities_fix', 'waiting_pi']);
const TERMINAL_REQUEST_STATES = new Set(['done', 'failed', 'cancelled', 'rejected', 'review_pending']);
// Cards that park an active request as waiting_pi (server.py PI_DECISION_KINDS). A resume card is one too, but its
// request is interrupted, not parked.
const PI_DECISION_KINDS = new Set(['clarify', 'question', 'scope', 'budget', 'research_plan', 'research_evidence', 'research_continue', 'result_storage', 'result_publish']);
const isActiveRequest = status => ACTIVE_REQUEST_STATES.has(status);
const isTerminalRequest = status => TERMINAL_REQUEST_STATES.has(status);
const websocketCloseAction = code => code === 1008
  ? {connection: 'auth', retry: false}
  : {connection: 'offline', retry: true};
// One label per gateway request status (R14). An unknown status shows as itself, never as 실패.
const REQUEST_STATUS_KO = { running: '진행 중', waiting_for_runner: '러너 기다림', waiting_quota: '한도 대기',
  waiting_login: '로그인 대기', waiting_facilities_fix: '환경 수정 승인 대기', waiting_pi: 'PI 결정 대기',
  interrupted: '중단됨', done: '완료', failed: '실패', cancelled: '취소됨', rejected: '거부됨', review_pending: '보고서 검토 대기' };
const requestStatusLabel = status => REQUEST_STATUS_KO[status] || String(status || '상태 모름');
const needsPiReview = q => !!q && isTerminalRequest(q.status) && q.pi_review_pending === true && !q.pi_reviewed_at;
const reviewLabel = q => ['done', 'review_pending'].includes(q.status) ? '보고서 검토' : '결과 확인';
const requestActivity = q => Math.max(Number(q.updated_at) || 0, Number(q.created_at) || 0);
function sortRequests(requests) {
  return [...requests].sort((a, b) => Number(isTerminalRequest(a.status) && !needsPiReview(a)) - Number(isTerminalRequest(b.status) && !needsPiReview(b)) ||
    requestActivity(b) - requestActivity(a) || (Number(b.created_at) || 0) - (Number(a.created_at) || 0) ||
    String(a.id).localeCompare(String(b.id)));
}
const requestResumeLabel = q => q?.resumed && q.status === 'running' ? '이어서 진행 중' : '';
// How a request is handled, for the 2.5D board and the 3D request row. A direct request never gets a route decision;
// a request that is no longer active and has none (failed before routing) shows no label, not "결정 중".
function processingLabel(q) {
  const p = q.processing || {};
  if (q.mode === 'direct') return '처리: 직접 맡김';
  if (p.fallback) return '처리: 단독 실패 → 팀';
  if (p.mode === 'solo') return `처리: 단독(${p.agent_id})`;
  if (p.mode === 'team') return '처리: 팀';
  return isActiveRequest(q.status) ? '처리: 결정 중' : '';
}
// The status with what the PI can do about it: resume an interrupted request, or which runner is still missing.
function requestStatusText(q, approvals) {
  if (!q) return '';
  if (needsPiReview(q)) return `${reviewLabel(q)} 대기`;
  const label = requestStatusLabel(q.status);
  if (q.status === 'interrupted') {
    const card = [...(approvals ? approvals.values() : [])].some(a => a && a.kind === 'resume' && a.request_id === q.id);
    return card ? `${label} · 결정 탭의 재개 카드로 이어 가요` : label;
  }
  if (q.status === 'waiting_for_runner' && (q.resumeMissing || []).length)
    return `${label} · 연결 필요: ${q.resumeMissing.join(', ')}`;
  return label;
}
// Cost text (#270): confirmed, price-table estimate and unaccounted tasks stay apart; an unknown is never $0.
const usd = v => `$${(Number(v) || 0).toFixed(2)}`;
function costParts(actual, estimated, unknown) {
  const parts = [];
  if (actual > 0 || !(estimated > 0 || unknown)) parts.push(`확인 ${usd(actual)}`);
  if (estimated > 0) parts.push(`추정 ${usd(estimated)}`);
  if (unknown) parts.push(`미집계된 비용 ${unknown}건`);
  return parts.join(' + ');
}
const summaryParts = s => costParts(Number(s.actual_usd) || 0, Number(s.estimated_usd) || 0, Number(s.unknown_count) || 0);
function costLabel(q) {
  if (q && q.costSummary) return summaryParts(q.costSummary);
  const cost = Number(q && q.cost) || 0;
  return q && q.costKnown === false ? `${cost ? usd(cost) + ' + ' : ''}미집계된 비용` : usd(cost);
}
function engineCostLabel(summary) {
  if (!summary || !summary.by_engine) return '';
  const engines = Object.entries(summary.by_engine).map(([name, e]) => `${name} ${summaryParts(e)}`).join(' · ');
  const stale = (summary.prices || []).filter(p => p && p.stale).map(p => `${p.engine} ${p.model} (${p.checked_on} 확인)`);
  return [engines, Number(summary.estimated_usd) > 0 ? '추정은 API 가격표 환산이며 청구액이 아닙니다' : '',
    stale.length ? `가격표 오래됨: ${stale.join(', ')}` : ''].filter(Boolean).join('; ');
}
// Who spent it: the request's cost per staff member, when the gateway recorded agent ids (by_agent).
function agentCostLabel(summary, name = id => id) {
  if (!summary || !summary.by_agent) return '';
  return Object.entries(summary.by_agent)
    .map(([id, row]) => `${id === 'unknown' ? '직원 기록 없음' : name(id)} ${summaryParts(row)}`).join(' · ');
}
// One staff member's spending across the requests this screen holds (#57 ⑦: the sheet's cumulative cost).
function agentSpentLabel(requests, id) {
  let actual = 0, estimated = 0, unknown = 0, count = 0;
  for (const q of requests) {
    const row = q && q.costSummary && q.costSummary.by_agent && q.costSummary.by_agent[id];
    if (!row) continue;
    count += 1; actual += Number(row.actual_usd) || 0; estimated += Number(row.estimated_usd) || 0;
    unknown += Number(row.unknown_count) || 0;
  }
  return count ? `${costParts(actual, estimated, unknown)} (요청 ${count}건)` : '';
}
function totalCostLabel(requests, total) {
  let estimated = 0, unknown = 0, legacyUnknown = false;
  for (const q of requests) {
    if (q.costSummary) { estimated += Number(q.costSummary.estimated_usd) || 0; unknown += Number(q.costSummary.unknown_count) || 0; }
    else if (q.costKnown === false) legacyUnknown = true;
  }
  return `사용 비용 ${usd(total)}${estimated > 0 ? ` (추정 ${usd(estimated)} 포함)` : ''}` +
    (unknown ? ` + 미집계된 비용 ${unknown}건${legacyUnknown ? ' 이상' : ''}` : legacyUnknown ? ' + 미집계된 비용' : '');
}
function applyCostSummary(q, summary) {
  if (!summary || typeof summary !== 'object') return;
  q.costSummary = summary; q.costKnown = !(Number(summary.unknown_count) > 0);
}
function createOfficeState({mode = 'live', now = () => Date.now() / 1000, focusStorage = null} = {}) {
let preferred = focusStorage?.get() || null;
const S = {
  agents: new Map(), approvals: new Map(), suggestions: [], requests: new Map(), current: null,
  jobs: new Map(), feed: [], cost: 0, conn: mode === 'demo' ? 'demo' : 'connecting', projects: [],
  lastSay: {}, taskStep: new Map(), seq: 0, doorUntil: 0,
};
// Task detail is derived from events and may be rebuilt from a snapshot.
// Keep it out of legacy state serialization while exposing it to both UIs.
Object.defineProperty(S, 'stepDetails', { value: new Map(), enumerable: false });
Object.defineProperty(S, 'askDetails', { value: new Map(), enumerable: false });
Object.defineProperty(S, 'engineHolds', { value: new Map(), enumerable: false });
// #36 additions stay non-enumerable too, so the legacy state digests (tests/web_state.cjs) are unchanged.
Object.defineProperty(S, 'defaultRefs', { value: [], writable: true, enumerable: false });
// Connected runner ids (R15), known once a snapshot said so; the page shows 러너 꺼짐 while it is empty.
Object.defineProperty(S, 'runners', { value: new Set(), writable: true, enumerable: false });
Object.defineProperty(S, 'runnersKnown', { value: false, writable: true, enumerable: false });
// #59 ①: each task's steer/pause/halt state (agent.control), for the staff sheet; out of the legacy digests too.
Object.defineProperty(S, 'taskControl', { value: new Map(), enumerable: false });
// Each task's kind from its dispatch (#59 ①): only step and direct turns get a control row.
Object.defineProperty(S, 'taskKind', { value: new Map(), enumerable: false });
Object.defineProperty(S, 'demoStatus', { value: null, writable: true, enumerable: false });
Object.defineProperty(S, 'activeTasks', { value: new Map(), enumerable: false });
function resetSnapshotState() {
  S.agents.clear(); S.approvals.clear(); S.suggestions = []; S.requests.clear(); S.current = null;
  S.jobs.clear(); S.feed = []; S.cost = 0; S.projects = [];
  S.lastSay = {}; S.taskStep.clear(); S.stepDetails.clear(); S.askDetails.clear(); S.engineHolds.clear(); S.seq = 0; S.doorUntil = 0;
  S.taskControl.clear(); S.taskKind.clear();
  S.defaultRefs = [];
  S.activeTasks.clear();
}
const STATE_KO = { idle: '쉬는 중', queued: '순서 기다림', working: '작업 중', waiting: '승인 기다림',
  hibernating: 'HPC 기다리는 중', done: '완료', error: '문제 발생' };
const KIND_KO = { result_storage: '결과 저장 위치', result_publish: '편집자 정리본 확인', hpc_submit: 'HPC 제출', tool_permission: '도구 권한', budget: '예산 초과', recruit: '채용', download: '대용량 다운로드', clarify: 'PI 질문', scope: '범위 확인', research_plan: 'CP1 계획 승인', research_evidence: 'CP2 증거 검토', research_continue: '리뷰 뒤 이어 가기', codex_sandbox_setup: 'Codex sandbox 준비', facilities_fix: '환경 자동 수정', resume: '중단된 요청 재개', question: '직원 질문 · PI 확인' };
const JOB_KO = { queued: '대기', running: '실행 중', completed: '완료', failed: '실패', held: '보류', suspended: '일시정지',
  cancelled: '취소', unknown_finished: '종료 (확인 필요)', error: '오류', missing: '확인 중' };
const GH_KO = { issue: 'GitHub에 이 요청의 이슈를 열었어요', plan: '이슈에 계획을 올렸어요', review: '이슈에 리뷰 결과를 올렸어요',
  report: '보고서를 저장소에 커밋했어요', final: '이슈에 최종 보고를 올리고 닫았어요', recruit: '이슈에 파견직 합류를 기록했어요',
  pr: 'PR을 열었어요', codex_review: 'PR에 "@codex review" 코멘트를 남겼어요' };
const PHASES = [['briefing', '브리핑'], ['plan', '계획'], ['execute', '실행'], ['review', '리뷰'], ['report', '보고']];
// The same Korean names the 2.5D phase list shows; the internal key never reaches the PI.
const phaseLabel = q => q.phase === 'done' ? '완료' : (PHASES.find(([key]) => key === q.phase) || [])[1] || String(q.phase);

const ag = id => S.agents.get(String(id || '').toLowerCase());
const hasPiDecision = a => [...S.approvals.values()].some(p => String(p.agent_id || '').toLowerCase() === a.id);
const completedForReview = a => [...S.requests.values()].some(q => needsPiReview(q) && (q.agent_id === a.id || q.plan.some(s => s.agent_id === a.id) || a.id === 'cso'));
const controlledState = t => {
  if (!t) return '';
  const c = S.taskControl.get(t.id) || t.control || {};
  return c.halted || t.state === 'halted' ? 'halted' : c.paused || t.state === 'paused' ? 'paused' : '';
};
// A queued consultation must not hide the command already occupying this employee.
const taskPriority = t => controlledState(t) ? 1 : ({error: 5, working: 4, running: 4, waiting: 3, queued: 2, hibernating: 1})[t.activity?.state || t.state] || 4;
const activeTask = a => [...S.activeTasks.values()].filter(t => t.agent_id === a.id)
  .sort((x, y) => taskPriority(y) - taskPriority(x) || (y.dispatched_at || 0) - (x.dispatched_at || 0))[0];
const currentTask = a => activeTask(a)?.title || activeTask(a)?.activity?.task || a.task || activeTask(a)?.step_id || activeTask(a)?.id || '';
const currentTool = a => controlledState(activeTask(a)) ? null : Object.values(activeTask(a)?.activity?.tools || {}).sort((x, y) => y.at - x.at)[0];
const warning = a => activeTask(a)?.activity?.warning ||
  (activeTask(a) && S.runnersKnown && a.runner_id && !S.runners.has(String(a.runner_id)) ? 'runner 연결 끊김' : '');
const taskState = a => {
  const t = activeTask(a); if (!t) return '';
  if (controlledState(t)) return 'waiting';
  if ([...S.approvals.values()].some(p => p.agent_id === a.id && (!p.task_id || p.task_id === t.id))) return 'waiting';
  const state = t.activity?.state || t.state;
  if (state === 'waiting') return 'hibernating';
  return ['queued', 'waiting', 'hibernating', 'error'].includes(state) ? state : 'working';
};
const visual = a => warning(a) ? 'error' : taskState(a) || (hasPiDecision(a) ? 'waiting' : completedForReview(a) ? 'done' : a.state === 'waiting' ? (a.waitFor === 'answer' ? 'hibernating' : 'idle') : a.state === 'done' && now() - (a.stateAt || 0) > 3
  ? 'idle' : STATE_KO[a.state] ? a.state : 'idle');
// 'hibernating' covers HPC jobs and unanswered asks (runner agent.status: jobs, asks); a PI question is not HPC.
const stateText = (a, vs = visual(a)) => {
  if (warning(a)) return warning(a);
  const t = activeTask(a), activity = t?.activity || {}, tool = currentTool(a);
  if (t) {
    if (controlledState(t) === 'halted') return '멈춤 · 재개 가능';
    if (controlledState(t) === 'paused') return '일시 정지됨';
    if (vs === 'queued') return '차례 기다리는 중';
    if (vs === 'waiting') return activity.ask ? '답 기다리는 중' : 'PI 결정 기다리는 중';
    if (vs === 'hibernating') return (activity.jobs || []).length ? 'HPC 작업 기다리는 중' : '답 기다리는 중';
    if (vs === 'working') return tool ? `${/Bash|PowerShell|shell|command_execution/i.test(tool.name) ? '명령' : '도구'} 실행 중 · ${Math.max(0, Math.floor((now() - tool.at) / 60))}분째` : '생각하는 중';
  }
  if (vs === 'done' && completedForReview(a)) return '작업 끝 · PI 검토 대기';
  return vs === 'hibernating' && a.waitFor === 'answer' ? '답 기다리는 중' : STATE_KO[vs];
};
function toolInputLine(input) {
  let value = input;
  if (typeof value === 'string') { try { value = JSON.parse(value); } catch (_) { /* a shell string */ } }
  if (value && typeof value === 'object') value = value.command ?? value.file_path ?? value.path ?? value.query ?? value.pattern ?? value.description ?? Object.keys(value).join(', ');
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}
function trackActivity(ev) {
  const d = ev.data || {}, tid = ev.task_id;
  if (!tid) return;
  if (ev.type === 'task.dispatched' && ev.request_id && (d.step_id || d.kind)) {
    for (const [oldId, old] of S.activeTasks) if (oldId !== tid && old.request_id === ev.request_id &&
      old.step_id === (d.step_id || d.kind)) S.activeTasks.delete(oldId);
  }
  if (ev.type === 'task.dispatched') S.activeTasks.set(tid, {id: tid, agent_id: ev.agent_id,
    request_id: ev.request_id, step_id: d.step_id || d.kind, title: d.title || short(stripPrompt(d.prompt), 60), dispatched_at: ev.ts,
    state: 'queued', activity: {state: 'queued', tools: {}}});
  if (['request.step_resumed', 'request.step_halt_expired', 'request.step_unpaused'].includes(ev.type)) {
    S.activeTasks.delete(tid); return;
  }
  if (['request.step_paused', 'request.step_halted'].includes(ev.type)) {
    const previous = S.activeTasks.get(tid) || {};
    S.activeTasks.set(tid, {...previous, id: tid, agent_id: ev.agent_id, request_id: ev.request_id,
      step_id: d.step_id, state: ev.type === 'request.step_paused' ? 'paused' : 'halted', activity: {}});
  }
  const t = S.activeTasks.get(tid); if (!t) return;
  const activity = t.activity ||= {}, ts = ev.ts || now();
  if (ev.type === 'agent.status') {
    if (!['done', 'idle'].includes(d.state)) {
      Object.assign(activity, d); activity.ask = d.ask || ''; activity.approval = d.approval || '';
      activity.jobs = d.jobs || []; activity.asks = d.asks || [];
      if (d.state === 'working') delete activity.warning;
    }
  } else if (ev.type === 'agent.tool') {
    if (['queued', 'waiting'].includes(activity.state)) activity.state = 'working';
    activity.tools ||= {}; if (!d.completed) activity.tools[d.id || 'legacy'] = {...d, at: ts}; delete activity.warning;
  } else if (ev.type === 'agent.tool_result') {
    delete (activity.tools || {})[d.id || 'legacy']; delete activity.warning;
    activity.state = 'working'; activity.ask = ''; activity.approval = '';
  } else if (ev.type === 'agent.log') {
    if (['warn', 'alert'].includes(d.level)) activity.warning = d.text;
    else if (!['debug', 'thinking'].includes(d.level)) delete activity.warning;
  } else if (ev.type === 'task.result') {
    if (d.error_kind === 'halted') { t.state = 'halted'; t.activity = {}; }
    else if ((d.pending_jobs || []).length || (d.pending_asks || []).length) {
      t.state = 'hibernating'; t.activity = {state: 'hibernating', jobs: d.pending_jobs || [], asks: d.pending_asks || []};
    } else S.activeTasks.delete(tid);
  }
}
const nick = id => { const key = String(id || '').toLowerCase(), a = ag(key); return ({cso:STAFF_NAMES.cso, facilities:STAFF_NAMES.facilities}[key]) || (a?.name && a.name !== a.id ? String(a.name).split(' ')[0] : STAFF_NAMES[key] || (key.startsWith('c_') ? '파견 직원' : id ? '직원' : '')); };
// A staff member's task rows: kept while the task runs, waits for HPC or an answer, is halted, or holds its paused
// step; removed on its terminal event, as a reload would (PR #532 review).
function trackTask(a, tid, state, title, keep = false, rid = '', sid = '') {
  if (!a || !tid) return;
  const prev = a.tasks.get(tid) || {}, control = S.taskControl.get(tid) || {};
  if (['done', 'error', 'idle'].includes(state) && !keep && !control.halted) a.tasks.delete(tid);
  else a.tasks.set(tid, { state, title: title || prev.title || '', rid: rid || prev.rid || '', sid: sid || prev.sid || '',
    kind: S.taskKind.get(tid) || prev.kind || '' });
}
function dropTask(a, tid) {
  if (a && tid && !(S.taskControl.get(tid) || {}).halted) { a.tasks.delete(tid); S.taskControl.delete(tid); }
}
function upsertAgent(a) {
  const prev = S.agents.get(a.id) || { state: 'idle', task: '', say: '', tool: '', log: [] };
  const next = { ...prev, ...a }, usage = next.usage || {}, toolCalls = next.toolCalls || 0;
  delete next.usage; delete next.toolCalls;
  Object.defineProperty(next, 'usage', { value: usage, writable: true, enumerable: false });
  Object.defineProperty(next, 'toolCalls', { value: toolCalls, writable: true, enumerable: false });
  // Not part of the replayed state: the trace survives roster updates but stays out of state comparisons.
  Object.defineProperty(next, 'trace', { value: prev.trace || [], writable: true, enumerable: false });
  Object.defineProperty(next, 'waitFor', { value: prev.waitFor || '', writable: true, configurable: true, enumerable: false });
  // The staff member's live tasks, task id -> {state, title} (#59 ①): two can run at once, and the staff sheet's
  // control rows each act on one of them (PR #532 review).
  Object.defineProperty(next, 'tasks', { value: prev.tasks || new Map(), writable: true, configurable: true, enumerable: false });
  S.agents.set(a.id, next);
}
function req(rid) {
  if (!S.requests.has(rid)) {
    const q = { id: rid, text: '', steps: {}, plan: [], github: [], cost: 0, costKnown: true, phase: 'briefing', status: 'running', created_at: now() };
    for (const key of ['pi_review_pending', 'pi_reviewed_at', 'agent_id', 'final_checkpoint']) Object.defineProperty(q, key, {value:null, writable:true, enumerable:false});
    Object.defineProperty(q, 'updated_at', { value: 0, writable: true, enumerable: false });
    Object.defineProperty(q, 'resumed', { value: false, writable: true, enumerable: false });
    Object.defineProperty(q, 'references', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'followups', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'piNotes', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'assumptions', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'processing', { value: null, writable: true, enumerable: false });
    Object.defineProperty(q, 'costSummary', { value: null, writable: true, enumerable: false });  // #270
    Object.defineProperty(q, 'pipelinePr', { value: null, writable: true, enumerable: false });
    Object.defineProperty(q, 'bundlePath', { value: '', writable: true, enumerable: false });
    Object.defineProperty(q, 'bundleStatus', { value: '', writable: true, enumerable: false });
    Object.defineProperty(q, 'bundleGrade', { value: '', writable: true, enumerable: false });
    Object.defineProperty(q, 'bundleGradeReasons', { value: [], writable: true, enumerable: false });
    Object.defineProperty(q, 'bundleWarning', { value: '', writable: true, enumerable: false });
    Object.defineProperty(q, 'skipWarningsShown', { value: new Set(), writable: true, enumerable: false });  // #446
    Object.defineProperty(q, 'resumeMissing', { value: [], writable: true, enumerable: false });  // R14
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
  q.assumptions = Array.isArray(plan.assumptions) ? plan.assumptions.filter(value => typeof value === 'string' && value.trim()).slice(0, 8) : [];
  if (plan.processing && typeof plan.processing === 'object') q.processing = { ...plan.processing };
  for (const s of q.plan) if (!q.steps[s.id]) q.steps[s.id] = 'pending';
}
function receivedText(row, text, metadata = {}) {
  Object.defineProperties(row, {
    fullText: {value: String(text ?? ''), enumerable: false},
    truncated: {value: Boolean(metadata.text_truncated || metadata.input_truncated), enumerable: false},
    cutChars: {value: metadata.text_limit ?? metadata.input_limit ?? [...String(metadata.text ?? metadata.input ?? text ?? '')].length, enumerable: false},
  });
  return row;
}
function logTo(a, text, ts, metadata = {}) {
  a.log.push(receivedText({ts, text: metadata.previewText || short(text, 240)}, text, metadata));
  if (a.log.length > 40) a.log.shift();
}
// #57 ⑥: thinking and debug lines stay out of the feed and activity, in their own ring buffer for the staff sheet.
function traceTo(a, level, text, ts, metadata = {}) {
  a.trace.push(receivedText({ ts, level, text: short(text, 400) }, text, metadata)); if (a.trace.length > 60) a.trace.shift();
}
function feed(item, ts, rid) {
  const {fullText, truncated, cutText, ...preview} = item;
  S.feed.unshift(receivedText({...preview, ts: ts || now(), rid}, fullText ?? item.text, {text_truncated: truncated, text: cutText ?? fullText ?? item.text}));
  if (S.feed.length > 120) S.feed.length = 120;
}
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
// Its ranking (server.py _sync_hold_status) puts an open PI-decision card below those holds, and lifting one sends
// no status event either, so a request with such a card falls back to waiting_pi, not running (#521).
function syncHoldStatus(q) {
  const states = Object.values(q.steps);
  if (states.includes('waiting_login') && !isTerminalRequest(q.status)) q.status = 'waiting_login';
  else if (states.includes('waiting_quota') && !isTerminalRequest(q.status)) q.status = 'waiting_quota';
  else if (states.includes('waiting_facilities_fix') && !isTerminalRequest(q.status)) q.status = 'waiting_facilities_fix';
  else if (['waiting_login', 'waiting_quota', 'waiting_facilities_fix'].includes(q.status))
    // A paused or halted step holds the request for the PI too, with no card (PR #532 verification).
    q.status = states.includes('paused') || states.includes('halted') ||
      [...S.approvals.values()].some(a => a.request_id === q.id && PI_DECISION_KINDS.has(a.kind)) ? 'waiting_pi' : 'running';
}
// A resumed request leaves the 'done' phase a snapshot gave it while it was interrupted (R14).
function reopen(q, status) {
  if (isTerminalRequest(q.status)) return;
  q.status = status;
  if (q.phase === 'done') q.phase = q.mode === 'direct' || q.plan.length ? 'execute' : 'briefing';
}
function selectRequest(id, manual = true) {
  if (!S.requests.has(id)) return;
  S.current = id;
  preferred = {id, manual};
  focusStorage?.set(preferred);
}
function pickCurrent() {
  const saved = preferred && S.requests.has(preferred.id) ? preferred : null;
  const id = saved?.id || sortRequests(S.requests.values())[0]?.id;
  if (id) selectRequest(id, saved?.manual || false);
  else { S.current = null; preferred = null; focusStorage?.set(null); }
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
  if (d.kind && ev.task_id) S.taskKind.set(ev.task_id, d.kind);
  if (q) {
    const ph = { briefing: 'briefing', plan: 'plan', review: 'review', synthesis: 'report', direct: 'execute' }[d.kind];
    if (ph) q.phase = ph;
    // A planned research request waits at CP1 in 계획; it is in 실행 once a step is dispatched.
    if (d.step_id) { q.steps[d.step_id] = 'working'; q.phase = 'execute'; }
  }
  const wake = /HPC 결과/.test(d.title || '');
  let m = null;
  if (d.kind === 'briefing') m = { who: 'cso', to: id, text: '착수 브리핑 부탁해요' };
  else if (d.kind === 'review') m = { who: 'cso', to: id, text: '결과 검토 부탁해요' };
  else if (d.kind === 'direct') m = { who: 'pi', to: id, text: short(d.title || d.prompt, 110), fullText: d.title || d.prompt };
  else if (wake) m = { who: 'hpc', to: id, text: '작업이 끝났어요. 이어서 해 주세요' };
  else if (d.kind === 'step' || !d.kind) m = { who: 'cso', to: id, text: short(d.title || stripPrompt(d.prompt), 110), fullText: d.title || stripPrompt(d.prompt) };
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
  trackActivity(ev);
  switch (t) {
    case 'messenger.translated': {
      const card = S.approvals.get(d.approval_id);
      if (card) card.pi_display = d.pi_display;
      break;
    }
    case 'snapshot': {
      const pendingBefore = [...S.approvals.keys()];
      // Reconnection keeps a view selected even by older UI consumers that assign S.current directly.
      if (S.current && S.current !== preferred?.id) preferred = {id:S.current, manual:true};
      resetSnapshotState();
      (d.agents || []).forEach(upsertAgent);
      S.projects = d.projects || [];
      S.defaultRefs = d.default_references || [];
      (d.recent_events || []).forEach(e => apply(e, true));
      S.demoStatus = d.demo_status || null;
      (d.engine_holds || []).forEach(hold => S.engineHolds.set(hold.engine, {...hold}));
      if (Array.isArray(d.runners)) { S.runners = new Set(d.runners.map(String)); S.runnersKnown = true; }
      for (const r of d.requests || []) {
        const q = req(r.id);
        Object.assign(q, { text: r.text, status: r.status, mode: r.mode, project_id: r.project_id, created_at: r.created_at, cost: r.cost_usd || 0, costKnown: r.cost_known !== false });
        q.pi_review_pending = r.pi_review_pending === true; q.pi_reviewed_at = r.pi_reviewed_at || null; q.agent_id = r.agent_id || '';
        q.final_checkpoint = r.final_checkpoint || null;
        q.updated_at = Math.max(q.updated_at, Number(r.updated_at) || 0);
        // With a trimmed event history, running in the initial snapshot still means work is continuing.
        q.resumed = q.resumed || r.status === 'running';
        if (r.route_decision && typeof r.route_decision === 'object') q.processing = { ...r.route_decision };
        if (typeof r.report === 'string') q.report = r.report;
        if (typeof r.error === 'string' && r.error) q.error = r.error;
        if (typeof r.report_appendix === 'string') q.report_appendix = r.report_appendix;
        q.bundlePath = r.bundle_path || ''; q.bundleStatus = r.bundle_status || ''; q.bundleGrade = r.bundle_grade || ''; q.bundleGradeReasons = r.bundle_grade_reasons || []; q.bundleWarning = r.bundle_warning || '';
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
        // recent_events may no longer hold a long request's plan event: a planned active request is past briefing.
        // With no step started and no review yet it is still in 계획, waiting for the PI to approve the plan (CP1).
        if (isActiveRequest(r.status) && (q.phase === 'briefing' || q.phase === 'plan') && q.plan.length) {
          const started = Object.values(q.steps).some(status => status !== 'pending');
          q.phase = r.review ? (r.review.verdict !== 'revise' ? 'review' : 'execute') : started ? 'execute' : 'plan';
        }
        if (r.pipeline_pr) setPipelinePr(q, r.pipeline_pr);  // the stored status outlives the replayed events
        if (!isActiveRequest(r.status)) q.phase = 'done';
      }
      const restored = new Map();
      const staffTasks = d.staff_tasks || d.running_tasks;
      if (Array.isArray(staffTasks)) {
        S.activeTasks.clear();
        for (const task of staffTasks) S.activeTasks.set(task.id, {...task,
          activity: {...(task.activity || {}), tools: {...(task.activity?.tools || {})}}});
      }
      for (const task of d.running_tasks || []) {
        if (!task.request_id || !task.step_id) continue;
        const key = `${task.request_id}:${task.step_id}`, previous = restored.get(key);
        const rank = [task.state === 'running' ? 1 : 0, Number(task.dispatched_at) || 0];
        if (previous && (previous.rank[0] > rank[0] ||
            (previous.rank[0] === rank[0] && previous.rank[1] > rank[1]))) continue;
        restored.set(key, { task, rank });
      }
      for (const { task } of restored.values()) {
        if (task.control || task.state === 'halted') S.taskControl.set(task.id, { ...(task.control || {}), ...(task.state === 'halted' ? { halted: true } : {}) });
        if (task.kind) S.taskKind.set(task.id, task.kind);
        trackTask(task.agent_id && ag(task.agent_id), task.id,
          task.state === 'running' ? 'working' : ['halted', 'paused'].includes(task.state) ? 'idle' : task.state, '', true,
          task.request_id, task.step_id);
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
    case 'runner.online': S.runners.add(String(d.runner_id)); feed({ who: 'system', text: `러너 연결됨 (${d.runner_id})` }, ts); break;
    // reason gateway_restarted: logged by a restarted gateway for a runner the old process saw online (#521).
    case 'runner.offline': S.runners.delete(String(d.runner_id)); feed(d.reason === 'gateway_restarted'
      ? { who: 'system', text: `게이트웨이가 다시 시작돼 러너 연결을 기다려요 (${d.runner_id})` }
      : { who: 'system', text: `러너 연결 끊김 (${d.runner_id})`, cls: 'alert' }, ts); break;
    case 'agent.status': {
      const a = ag(id); if (!a) break;
      a.state = d.state || a.state; a.stateAt = ts;
      trackTask(a, ev.task_id, d.state || a.state, d.task, Boolean(d.halted), rid, S.taskStep.get(ev.task_id));
      // Display only, like usage and trace: kept out of the replayed state the golden hashes compare.
      if (['hibernating', 'waiting', 'working', 'idle', 'done'].includes(d.state)) Object.defineProperty(a, 'waitFor', { writable: true, configurable: true, enumerable: false,
        value: (d.jobs || []).length ? 'hpc' : d.ask || (d.asks || []).length ? 'answer' : '' });
      if (d.task) a.task = d.task;
      if (d.state === 'done' || d.state === 'idle') a.tool = '';
      if (d.error) { a.error = d.error; logTo(a, `오류: ${d.error}`, ts); }
      if (d.state === 'done' && !replay) effects.push({ type: 'renderAfter', ms: 3200 });
      break;
    }
    case 'agent.tool': { const a = ag(id); if (!a) break; a.tool = d.name; a.toolAt = ts; a.toolCalls = (a.toolCalls || 0) + 1;
      const input = typeof d.input === 'string' ? d.input : JSON.stringify(d.input ?? '');
      logTo(a, `도구 ${toolLabel(d.name)} ${input}`, ts, {...d, previewText: `도구 ${toolLabel(d.name)} ${short(d.summary || toolInputLine(d.input), 80)}`}); break; }
    case 'agent.control': {
      // Merged, never replaced: a steer or pause to a halted task keeps it halted until resumed or expired.
      if (ev.task_id) {
        const prev = S.taskControl.get(ev.task_id) || {};
        S.taskControl.set(ev.task_id, { ...prev, ...d, halted: Boolean(prev.halted || d.halted) });
      }
      if ((d.undelivered || []).length) {  // the step ended before a turn could take these
        const text = `전달 못 한 방향 수정 ${d.undelivered.length}개: ${short(d.undelivered.join(' / '), 160)}`;
        const a = ag(id); if (a) logTo(a, text, ts);
        feed({ who: id || 'system', text, cls: 'alert' }, ts, rid);
      }
      break;
    }
    case 'request.step_paused': {  // the step's next turn waits for the PI; its last task's row resumes it
      if (ev.task_id) S.taskControl.set(ev.task_id, { ...(S.taskControl.get(ev.task_id) || {}), paused: true });
      trackTask(ag(id), ev.task_id, 'idle', '', true, rid, d.step_id);
      if (rid && d.step_id) req(rid).steps[d.step_id] = 'paused';
      break;
    }
    case 'request.step_unpaused': dropTask(ag(id), ev.task_id); break;
    case 'request.step_halted': {
      if (ev.task_id) S.taskControl.set(ev.task_id, { ...(S.taskControl.get(ev.task_id) || {}), halted: true });
      if (rid && d.step_id) req(rid).steps[d.step_id] = 'halted';
      const a = ag(id); if (a) { trackTask(a, ev.task_id, 'idle', '', true); logTo(a, 'PI가 멈췄어요. 재개하면 같은 세션으로 이어 가요', ts); }
      break;
    }
    case 'request.step_resumed': case 'request.step_halt_expired': {
      // Resumed: the next turn is a new task. Expired: the PI did not resume within the bound and the step goes on
      // as failed. Either way the halted task's resume row goes (PR #532 review).
      if (ev.task_id) S.taskControl.delete(ev.task_id);
      const a = ag(id); if (a && ev.task_id) a.tasks.delete(ev.task_id);
      if (rid && d.step_id && t === 'request.step_resumed') req(rid).steps[d.step_id] = 'working';
      if (rid && d.step_id && t === 'request.step_halt_expired' && req(rid).steps[d.step_id] === 'halted') req(rid).steps[d.step_id] = 'error';
      if (a && t === 'request.step_halt_expired') logTo(a, d.reason === 'cancelled' ? '요청을 취소해 멈춘 단계를 닫았어요'
        : '멈춘 단계를 재개 기한 안에 재개하지 않아 실패로 넘어갔어요', ts);
      break;
    }
    case 'agent.tool_error': { const a = ag(id); if (a) logTo(a, `도구 오류: ${d.text}`, ts, d); break; }
    case 'agent.log': {
      const a = ag(id);
      if (!a && d.code === 'dev_log_visibility' && d.text) {
        feed({who:'system', text:d.text, cls:'alert'}, ts, rid);
        if (!replay) effects.push({type:'toast', text:d.text});
        break;
      }
      if (!a || !d.text) break;
      if (d.level === 'debug' || d.level === 'thinking') { traceTo(a, d.level, d.text, ts, d); break; }
      a.say = d.text; a.sayAt = ts; logTo(a, d.text, ts, d);
      // An alert (a read-only run changed files) always reaches the feed, never throttled with ordinary talk.
      // R21: it also raises a toast, so a hold the PI must clear on the PC (UAC) is seen outside the messenger.
      if (d.level === 'alert') {
        feed({ who: id, text: short(d.text, 300), fullText: d.text, truncated: d.text_truncated, cls: 'alert' }, ts, rid);
        if (!replay) effects.push({ type: 'toast', text: `${nick(id)}: ${short(d.text, 90)}` });
        break;
      }
      if (!S.lastSay[id] || ts - S.lastSay[id] > 6) { S.lastSay[id] = ts; feed({ who: id, text: short(d.text, 150), fullText: d.text, truncated: d.text_truncated }, ts, rid); }
      break;
    }
    case 'agent.ask': {
      S.askDetails.set(d.id, { ...d, agent_id: d.agent_id || id });
      if (S.askDetails.size > 120) S.askDetails.delete(S.askDetails.keys().next().value);
      feed({ who: d.agent_id || id, to: String(d.to || '').replace(/^colleague:/, ''),
        text: `질문: ${short(d.question, 150)}`, fullText: `질문: ${d.question || ''}` }, ts, rid);
      break;
    }
    case 'agent.answer': {
      const ask = S.askDetails.get(d.ask_id) || {};
      const rejected = d.status === 'rejected';
      const question = d.question || ask.question;
      feed({ who: d.from || id, to: d.to || ask.agent_id,
        text: `${rejected ? '거절' : '답변'}: ${short(rejected ? d.reason : d.answer, 130)}${question ? ` (질문: ${short(question, 70)})` : ''}`,
        fullText: `${rejected ? '거절' : '답변'}: ${(rejected ? d.reason : d.answer) || ''}${question ? ` (질문: ${question})` : ''}`,
        cls: rejected ? 'alert' : '' }, ts, rid);
      break;
    }
    case 'demo.status': {
      S.demoStatus = {...d};
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
      // A new turn of the step supersedes the staff member's earlier row for it (an HPC wait, a held pause).
      const worker = ag(id), sid = d.step_id || d.kind;
      if (worker && rid && sid) for (const [tid, row] of [...worker.tasks]) if (tid !== ev.task_id && row.rid === rid && row.sid === sid) dropTask(worker, tid);
      if (rid && d.step_id) Object.assign(stepDetail(rid, d.step_id), {
        task_id: ev.task_id, attempts: Math.max(stepDetail(rid, d.step_id).attempts || 0, Number(d.attempt) || 1),
      });
      break;
    }
    case 'task.result': {
      const sid = S.taskStep.get(ev.task_id);
      if (d.error_kind === 'halted' && ev.task_id) S.taskControl.set(ev.task_id, { ...(S.taskControl.get(ev.task_id) || {}), halted: true });
      else if (!(d.pending_jobs || []).length && !(d.pending_asks || []).length) dropTask(ag(id), ev.task_id);  // over
      if (sid && rid) {
        if (d.ok === false) req(rid).steps[sid] = d.error_kind === 'halted' ? 'halted' : 'error';
        Object.assign(stepDetail(rid, sid), { task_id: ev.task_id, ok: d.ok, text: d.text || '', error: d.error || '',
          outputs: d.outputs || [], missing_outputs: d.missing_outputs || [] });
      }
      break;
    }
    case 'approval.requested': {
      S.approvals.set(d.id, { ...d, agent_id: d.agent_id || id, request_id: d.request_id || rid });
      if (d.kind === 'facilities_fix' && rid && d.detail?.step_id) {
        const q = req(rid); q.steps[d.detail.step_id] = 'waiting_facilities_fix'; syncHoldStatus(q);
      }
      // The gateway opens a resume card only for a request it has just marked interrupted (restart or
      // resume timeout) and sends no status event for that, so the card itself says so (R14).
      const resumeOf = d.kind === 'resume' && (d.request_id || rid);
      if (resumeOf && S.requests.has(resumeOf) && !isTerminalRequest(S.requests.get(resumeOf).status)) {
        req(resumeOf).status = 'interrupted';
      }
      feed({ who: d.agent_id || id || 'cso', text: `승인 요청: ${short(d.pi_summary || d.summary, 130)}`, fullText: `승인 요청: ${d.pi_summary || d.summary || ''}`, cls: 'alert' }, ts, rid);
      if (!replay) effects.push({ type: 'toast', text: `승인 요청이 왔어요: ${short(d.pi_summary || '작업 내용을 확인해 주세요.', 50)}`, approval_id: d.id });
      break;
    }
    case 'approval.expired': case 'approval.stale': endApproval(d.id, effects); break;
    case 'approval.resolved': { const pending = S.approvals.get(d.id); endApproval(d.id, effects); if (pending?.kind === 'facilities_fix' && rid && pending.detail?.step_id) { const q = req(rid); if (q.steps[pending.detail.step_id] === 'waiting_facilities_fix') q.steps[pending.detail.step_id] = 'pending'; syncHoldStatus(q); }
      // An approved resume card puts the request in waiting_for_runner at once (server.py), again without a status event.
      const resumed = pending?.kind === 'resume' && d.approved && (pending.request_id || rid);
      if (resumed && S.requests.has(resumed)) { const q = req(resumed); q.resumeMissing = []; reopen(q, 'waiting_for_runner'); }
      // A card nobody answered is not the PI's refusal (R5, #521): it timed out or was closed.
      const unanswered = d.state === 'timed_out' ? '시간 초과로 닫혔어요' : ['expired', 'cancelled'].includes(d.state) ? '답 없이 닫혔어요' : '';
      const note = d.note ? ` (${short(d.note, 60)})` : '';
      const fullNote = d.note ? ` (${d.note})` : '';
      feed(unanswered ? { who: 'system', text: `${unanswered}${note}`, fullText: `${unanswered}${fullNote}` }
        : { who: 'pi', text: d.approved ? '승인했어요' : `${d.choice === 'revise' ? '수정을 요청했어요' : '거절했어요'}${note}`,
          fullText: d.approved ? '승인했어요' : `${d.choice === 'revise' ? '수정을 요청했어요' : '거절했어요'}${fullNote}` }, ts, rid); break; }
    case 'job.submitted': S.jobs.set(String(d.job_id), { id: String(d.job_id), name: d.name, state: 'queued', agent: id, ts }); feed({ who: id, text: `HPC 작업 제출: ${d.name || ''} (${d.job_id})` }, ts, rid); break;
    case 'job.state': {
      const j = S.jobs.get(String(d.job_id)) || { id: String(d.job_id), name: d.name, agent: id };
      Object.assign(j, { state: d.state, exit: d.exit_status, ts }); S.jobs.set(j.id, j);
      break;
    }
    case 'jobs.finished': feed({ who: 'hpc', text: `작업이 끝나서 ${nick(id)}를 깨웠어요` }, ts, rid); break;
    case 'request.created': {
      const q = req(rid);
      Object.assign(q, { text: d.text, mode: d.mode, project_id: d.project_id, status: d.status || 'running', phase: isTerminalRequest(d.status) ? 'done' : d.mode === 'direct' ? 'execute' : 'briefing', created_at: ts });
      q.agent_id = d.agent_id || '';
      q.references = d.references || [];
      if (!replay && !(preferred?.manual && S.requests.has(S.current) && isTerminalRequest(q.status))) selectRequest(rid, false);
      feed({ who: 'pi', text: `새 요청: ${short(d.text, 130)}`, fullText: `새 요청: ${d.text || ''}` }, ts, rid);
      break;
    }
    case 'request.plan': {
      const q = req(rid); setPlan(q, d); q.phase = 'plan';  // 실행 starts with the first dispatched step (onDispatch)
      feed({ who: 'cso', text: `계획을 세웠어요: ${(d.steps || []).length}단계${(d.recruit || []).length ? ', 파견직 채용 제안 1건' : ''}` }, ts, rid);
      // #446: each topic checklist item the plan could not do reaches the PI with its reason, one line each.
      // A re-plan re-sends the whole list; only a warning not shown before for this request is posted.
      for (const w of d.warnings || []) {
        if (typeof w !== 'string' || !w.startsWith('점검 못 함 ') || q.skipWarningsShown.has(w)) continue;
        q.skipWarningsShown.add(w);
        feed({ who: 'cso', text: short(w, 300), fullText: w, cls: 'alert' }, ts, rid);
      }
      break;
    }
    case 'request.route': {
      const q = req(rid); q.processing = { ...d };
      feed({ who: 'cso', text: d.fallback ? '단독 처리가 끝나지 않아 팀 계획으로 이어가요'
        : d.mode === 'solo' ? `처리: 단독(${d.agent_id})` : '처리: 팀' }, ts, rid);
      break;
    }
    case 'request.step_attempt': {
      if (rid && d.step_id) stepDetail(rid, d.step_id).attempts = Math.max(stepDetail(rid, d.step_id).attempts || 0, Number(d.attempt) || 1);
      break;
    }
    case 'request.resume_waiting': {
      const q = req(rid); q.resumeMissing = (d.missing_agents || []).map(String); reopen(q, 'waiting_for_runner');
      feed({ who: 'system', text: `재개를 기다려요. 러너 연결이 필요한 직원: ${q.resumeMissing.join(', ') || '없음'}`, cls: 'alert' }, ts, rid);
      break;
    }
    case 'request.resumed': {
      const q = req(rid); q.resumeMissing = []; q.resumed = true; reopen(q, 'running');
      feed({ who: 'system', text: '중단된 요청을 다시 이어 가요' }, ts, rid);
      break;
    }
    case 'request.resume_timeout': {
      const q = req(rid);
      if (!isTerminalRequest(q.status)) q.status = 'interrupted';
      q.resumeMissing = [];
      feed({ who: 'system', text: `러너가 돌아오지 않아 재개를 멈췄어요(${(d.missing_agents || []).join(', ')}). 새 재개 카드가 떠요`, cls: 'alert' }, ts, rid);
      break;
    }
    case 'request.step_quota_wait': {
      const q = req(rid); q.steps[d.step_id] = 'waiting_quota';
      Object.assign(stepDetail(rid, d.step_id), { quota_resume_at: d.resume_at, quota_engine: d.engine,
        quota_deadline_at: d.deadline_at });
      syncHoldStatus(q);
      break;
    }
    case 'request.step_quota_resumed': {
      const q = req(rid), detail = stepDetail(rid, d.step_id);
      if (q.steps[d.step_id] === 'waiting_quota') q.steps[d.step_id] = 'pending';
      delete detail.quota_resume_at; delete detail.quota_engine; delete detail.quota_deadline_at;
      syncHoldStatus(q);
      break;
    }
    case 'request.step_login_wait': {
      const q = req(rid); q.steps[d.step_id] = 'waiting_login';
      Object.assign(stepDetail(rid, d.step_id), { login_resume_at: d.resume_at, login_engine: d.engine,
        login_reason: d.reason || '' });
      syncHoldStatus(q);
      break;
    }
    case 'request.step_login_resumed': {
      const q = req(rid), detail = stepDetail(rid, d.step_id);
      if (q.steps[d.step_id] === 'waiting_login') q.steps[d.step_id] = 'pending';
      delete detail.login_resume_at; delete detail.login_engine; delete detail.login_reason;
      syncHoldStatus(q);
      break;
    }
    case 'engine.login_wait': {
      S.engineHolds.set(d.engine, {...d, kind:'login'});
      feed({ who:'system', text:`${d.engine} 로그인이 필요해요. ${d.command || ''}`, cls:'alert' }, ts, rid);
      if (!replay) effects.push({ type:'toast', text:`${d.engine} 로그인 뒤 다시 시도해 주세요` });
      break;
    }
    case 'engine.login_resumed': {
      S.engineHolds.delete(d.engine);
      break;
    }
    case 'request.questions': feed({ who: 'cso', text: `확인이 필요해요: ${short((d.questions || []).join(' / '), 150)}`, fullText: `확인이 필요해요: ${(d.questions || []).join(' / ')}`, cls: 'alert' }, ts, rid); break;
    case 'request.facilities_fix': { const detail = stepDetail(rid, d.step_id), status = d.status || (d.ok ? 'succeeded' : 'failed'); detail.facilities_fix = { ...d, status }; const label = status === 'applied' ? '환경 수정 적용' : status === 'succeeded' ? '환경 수정 성공' : '환경 수정 실패'; feed({ who: 'facilities', text: `${label}: ${short((status === 'failed' ? d.error : d.action) || d.fix_id || '', 120)}`, fullText: `${label}: ${(status === 'failed' ? d.error : d.action) || d.fix_id || ''}`, cls: status === 'failed' ? 'alert' : '' }, ts, rid); break; }
    case 'request.step_done': { const q = req(rid), detail = stepDetail(rid, d.step_id); q.steps[d.step_id] = d.ok === false ? 'error' : 'done'; Object.assign(detail, { attempts: d.attempts || detail.attempts, error: d.reason || detail.error }); if (d.ok === false && d.environment) detail.environment = d.environment; else delete detail.environment; if (d.facilities_fix) detail.facilities_fix = d.facilities_fix; delete detail.quota_resume_at; delete detail.quota_engine; delete detail.quota_deadline_at; delete detail.login_resume_at; delete detail.login_engine; delete detail.login_reason; syncHoldStatus(q); break; }
    case 'request.step_skipped': { const q = req(rid); q.steps[d.step_id] = 'skipped'; stepDetail(rid, d.step_id).error = d.reason || ''; syncHoldStatus(q); break; }
    case 'request.review': {
      const q = req(rid), sc = d.scores || {};
      q.review = d; q.phase = d.verdict === 'revise' ? 'execute' : 'review';
      for (const step of q.plan) stepDetail(rid, step.id).review_issues = (d.issues || []).filter(issue => issue.step_id === step.id);
      if (d.verdict === 'revise') (d.issues || []).forEach(i => { if (i.step_id in q.steps) q.steps[i.step_id] = 'revise'; });
      const unparsed = d.status === 'review_unparsed';
      feed({ who: 'sci_reviewer', text: unparsed ? '리뷰 판정 실패. PI 확인이 필요해요'
        : d.verdict === 'revise' ? `수정 요청: ${short(((d.issues || [])[0] || {}).problem || '', 90)}`
        : d.verdict === 'accept' ? `통과. 질문 부합 ${sc.addresses_question}/5, 근거 ${sc.evidence}/5, 철저성 ${sc.thoroughness}/5`
        : '리뷰 판정 실패. PI 확인이 필요해요',
        fullText: !unparsed && d.verdict === 'revise' ? `수정 요청: ${((d.issues || [])[0] || {}).problem || ''}` : undefined,
        cls: unparsed ? 'alert' : '' }, ts, rid);
      break;
    }
    case 'recruit.suggested': {
      const key = d.repo || d.paper;
      const hired = [...S.agents.values()].some(a => isContract(a) && key && String(key).toLowerCase().includes(String(a.id).slice(2)));
      if (!hired && !S.suggestions.some(s => (s.repo || s.paper) === key)) S.suggestions.push({ ...d, request_id: rid, id: `sug_${++S.seq}` });
      feed({ who: 'cso', text: `파견직 채용을 제안해요: ${short(key, 70)}`, fullText: `파견직 채용을 제안해요: ${key || ''}` }, ts, rid);
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
      const pv = na.contract_provenance, hosts = pv ? (pv.external_services || []).map(s => s && s.host).filter(Boolean) : [];
      // #424: the commit it was built from and where its tools send data, said once at hiring.
      const built = pv ? ` · commit ${pv.source_commit ? String(pv.source_commit).slice(0, 7) : '미상'} · ${pv.code_license || 'license 미상'}` +
        ` · ${hosts.length ? `외부 전송 ${hosts.join(', ')}` : '외부 전송 없음'}` : '';
      feed({ who: 'recruiter', text: `${na.name || na.id} 입사했어요 (수습 ${d.passed_probation ? '통과' : '진행 중'})${built}` }, ts, rid);
      break;
    }
    case 'contract.warning':
      feed({ who: 'recruiter', text: `재고용한 ${d.agent_id || id}: ${(d.changed || []).join(', ')}이(가) 채용 때 hash와 맞지 않아요. 확인한 뒤 맡기세요`, cls: 'alert' }, ts, rid);
      break;
    case 'recruit.failed': feed({ who: 'recruiter', text: `채용하지 못했어요: ${short(d.error, 120)}`, fullText: `채용하지 못했어요: ${d.error || ''}`, cls: 'alert' }, ts, rid); break;
    // A status the gateway sets itself: waiting_pi, waiting_for_runner, interrupted, running (R5, R15, #494).
    case 'request.status': { const q = req(rid); if (d.status && !isTerminalRequest(q.status)) q.status = d.status; break; }
    case 'request.runner_wait': feed({ who: 'system', text: d.message || '러너를 기다려요', cls: 'alert' }, ts, rid); break;
    case 'request.reviewed': { const q = req(rid); q.pi_review_pending = d.pi_review_pending === true; q.pi_reviewed_at = d.pi_reviewed_at; if (d.status) q.status = d.status; if (d.final_checkpoint) q.final_checkpoint = d.final_checkpoint; break; }
    case 'request.completed': case 'request.failed': {
      for (const [tid, task] of S.activeTasks) if (task.request_id === rid) S.activeTasks.delete(tid);
      const q = req(rid), wasPending = needsPiReview(q);
      // The request is over (done, failed or cancelled): no step of it takes a control any more.
      for (const a of S.agents.values()) for (const [tid, row] of [...a.tasks]) if (row.rid === rid) { a.tasks.delete(tid); S.taskControl.delete(tid); }
      q.status = isTerminalRequest(d.status) ? d.status : t === 'request.completed' && d.ok !== false ? 'done' : 'failed'; q.phase = 'done';
      q.final_checkpoint = d.final_checkpoint || null;
      q.pi_review_pending = d.pi_review_pending === true; q.pi_reviewed_at = d.pi_reviewed_at || null;
      if (!replay && needsPiReview(q) && !wasPending) effects.push({type:'toast', text:`${q.status === 'done' ? '보고서가 준비됐어요' : '요청이 끝났어요'} · ${reviewLabel(q)} 대기`});
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
      q.bundlePath = d.bundle_path || ''; q.bundleStatus = d.bundle_status || ''; q.bundleGrade = d.bundle_grade || ''; q.bundleGradeReasons = d.bundle_grade_reasons || []; q.bundleWarning = d.bundle_warning || '';
      // The gateway sends a reason; when it did not, say what is known instead of ending the line on a colon.
      const why = short(d.error, 100);
      const failedText = why ? `요청이 실패했어요: ${why}` : d.report
        ? '요청을 완료로 보지 않았어요. 보고서와 실행 기록에서 이유를 확인하세요'
        : '요청이 끝까지 가지 못했어요. 실행 기록을 보세요';
      feed({ who: 'cso', text: q.status === 'review_pending' ? '최종 초안을 검토해 주세요 (CP4)' : q.status === 'done' ? '최종 보고서를 올렸어요' : q.status === 'cancelled' ? '요청을 취소했어요. 끝난 단계까지 보고서에 남겼어요' : failedText,
        fullText: q.status === 'failed' && d.error ? `요청이 실패했어요: ${d.error}` : undefined, cls: q.status === 'failed' ? 'alert' : '' }, ts, rid);
      break;
    }
    case 'request.bundle': {
      const q = req(rid);
      q.bundlePath = d.bundle_path || ''; q.bundleStatus = d.bundle_status || ''; q.bundleGrade = d.bundle_grade || ''; q.bundleGradeReasons = d.bundle_grade_reasons || []; q.bundleWarning = d.bundle_warning || '';
      break;
    }
    case 'request.storage': {
      req(rid).resultStorage = d.result_storage || {};
      feed({who:'editor', text:d.result_storage?.warning || (d.result_storage?.status === 'published' ? '정리본을 GitHub에 올렸습니다.' : '정리본을 로컬에 남겼습니다.')}, ts, rid);
      break;
    }
    case 'request.followup': {
      const q = req(rid), entry = { id: d.id, text: d.text, agent_id: d.agent_id, status: 'running', asked_at: ts };
      q.followups = [...q.followups.filter(f => f.id !== d.id), entry];
      feed({ who: 'pi', to: d.agent_id || 'cso', text: `이어 묻기: ${short(d.text, 130)}`, fullText: `이어 묻기: ${d.text || ''}` }, ts, rid);
      break;
    }
    case 'request.note': {
      const q = req(rid), entry = { id: d.id, text: d.text, at: d.at || ts };
      q.piNotes = [...q.piNotes.filter(note => note.id !== entry.id), entry];
      feed({ who: 'pi', to: 'cso', text: `실행 중 메모: ${short(d.text, 130)}`, fullText: `실행 중 메모: ${d.text || ''}` }, ts, rid);
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
        fullText: d.ok ? `답변: ${d.answer || ''}` : `이어 묻기에 답하지 못했어요: ${d.error || ''}`, truncated: d.answer_truncated, cutText: d.answer,
        cls: d.ok ? '' : 'alert' }, ts, rid);
      break;
    }
    case 'github.posted': { const q = req(rid); q.github.push({ kind: d.kind, url: d.url, number: d.number }); feed({ who: 'github', text: GH_KO[d.kind] || 'GitHub에 업데이트했어요', url: d.url }, ts, rid); break; }
    case 'github.failed': feed({ who: 'github', text: `GitHub 업데이트 실패: ${short(d.error, 110)}`, fullText: `GitHub 업데이트 실패: ${d.error || ''}`, cls: 'alert' }, ts, rid); break;
    case 'pipeline.pr': {
      setPipelinePr(req(rid), d);
      feed({ who: 'github', text: d.status === 'open' ? `새 pipeline PR을 열었어요: ${d.name}`
        : d.status === 'pending' ? `새 pipeline PR 대기: ${short(d.reason, 100)}`
        : `새 pipeline PR 거부: ${short(d.reason, 100)}`,
        fullText: d.status === 'open' ? undefined : `새 pipeline PR ${d.status === 'pending' ? '대기' : '거부'}: ${d.reason || ''}`,
        cls: d.status === 'open' ? '' : 'alert', url: d.url || undefined }, ts, rid);
      break;
    }
    default: break;
  }
  if (rid && S.requests.has(rid)) {
    const q = S.requests.get(rid);
    q.updated_at = Math.max(q.updated_at, Number(ev.ts) || 0);
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
  if (q && typeof request?.error === 'string' && q.error !== request.error) { q.error = request.error; filled++; }
  if (q && typeof request?.report === 'string' && (q.report_truncated || q.report !== request.report)) {
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

return { S, apply, selectRequest, ag, visual, stateText, currentTask, currentTool, toolInputLine, nick, req, setPlan, feed, fillFollowups, fillRequestDetail, toolLabel, STATE_KO, KIND_KO, JOB_KO, PHASES, phaseLabel };
}
root.LabHQState = { createOfficeState, costLabel, engineCostLabel, agentCostLabel, agentSpentLabel, totalCostLabel,
  isActiveRequest, isTerminalRequest, REQUEST_STATUS_KO, requestStatusLabel, requestStatusText, processingLabel,
  needsPiReview, reviewLabel, staffCharacter, STAFF_NAMES, websocketCloseAction, officeStorageKey, createRequestFocusStorage, sortRequests, requestActivity, requestResumeLabel, connectionLabel, demoBannerText };
})(globalThis);
