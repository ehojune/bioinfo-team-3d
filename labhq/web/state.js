// Shared event state, with no DOM, transport, or timers. Classic script also runs in Node.
(function (root) {
'use strict';
const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const isContract = a => !!a && (a.employment === 'contract' || String(a.id).startsWith('c_'));
function createOfficeState({mode = 'live', now = () => Date.now() / 1000} = {}) {
const S = {
  agents: new Map(), approvals: new Map(), suggestions: [], requests: new Map(), current: null,
  jobs: new Map(), feed: [], cost: 0, conn: mode === 'demo' ? 'demo' : 'connecting', projects: [],
  lastSay: {}, taskStep: new Map(), seq: 0, doorUntil: 0,
};
// Task detail is derived from events and may be rebuilt from a snapshot.
// Keep it out of legacy state serialization while exposing it to both UIs.
Object.defineProperty(S, 'stepDetails', { value: new Map(), enumerable: false });
function resetSnapshotState() {
  S.agents.clear(); S.approvals.clear(); S.suggestions = []; S.requests.clear(); S.current = null;
  S.jobs.clear(); S.feed = []; S.cost = 0; S.projects = [];
  S.lastSay = {}; S.taskStep.clear(); S.stepDetails.clear(); S.seq = 0; S.doorUntil = 0;
}
const STATE_KO = { idle: '쉬는 중', queued: '순서 기다림', working: '작업 중', waiting: '승인 기다림',
  hibernating: 'HPC 기다리는 중', done: '완료', error: '문제 발생' };
const KIND_KO = { hpc_submit: 'HPC 제출', tool_permission: '도구 권한', budget: '예산 초과', recruit: '채용', download: '대용량 다운로드', clarify: 'PI 질문' };
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
  if (!S.requests.has(rid)) S.requests.set(rid, { id: rid, text: '', steps: {}, plan: [], github: [], cost: 0, costKnown: true, phase: 'briefing', status: 'running', created_at: now() });
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
function pickCurrent() {
  const all = [...S.requests.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  S.current = (all.find(r => r.status === 'running') || all[0] || {}).id || null;
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
function apply(ev, replay = false) {
  const effects = [];
  const t = ev.type, d = ev.data || {}, id = ev.agent_id, rid = ev.request_id, ts = ev.ts || now();
  switch (t) {
    case 'snapshot': {
      resetSnapshotState();
      (d.agents || []).forEach(upsertAgent);
      S.projects = d.projects || [];
      (d.recent_events || []).forEach(e => apply(e, true));
      for (const r of d.requests || []) {
        const q = req(r.id);
        Object.assign(q, { text: r.text, status: r.status, mode: r.mode, project_id: r.project_id, created_at: r.created_at, cost: r.cost_usd || 0, costKnown: r.cost_known !== false });
        if (r.plan) setPlan(q, r.plan);
        Object.assign(q.steps, r.step_status || {});
        for (const [sid, detail] of Object.entries(r.step_details || {})) Object.assign(stepDetail(r.id, sid), detail);
        if (r.review) q.review = r.review;
        if (r.status !== 'running') q.phase = 'done';
      }
      S.cost = (d.requests || []).reduce((total, r) => total + (Number(r.cost_usd) || 0), 0);
      S.approvals.clear();
      (d.approvals || []).forEach(x => S.approvals.set(x.id, x));
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
      if (!S.lastSay[id] || ts - S.lastSay[id] > 6) { S.lastSay[id] = ts; feed({ who: id, text: short(d.text, 150) }, ts, rid); }
      break;
    }
    case 'agent.usage': {
      const c = Number(d.cost_usd) || 0;
      const a = ag(id);
      if (a) a.usage = { ...(a.usage || {}), ...(d.tokens || {}), ...('num_turns' in d ? { num_turns: d.num_turns } : {}) };
      if (c > 0) { S.cost += c; if (rid) req(rid).cost += c; }
      if (rid && d.cost_known === false) req(rid).costKnown = false;
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
      if (!replay) effects.push({ type: 'toast', text: `승인 요청이 왔어요: ${short(d.summary, 50)}` });
      break;
    }
    case 'approval.resolved': S.approvals.delete(d.id); feed({ who: 'pi', text: d.approved ? '승인했어요' : `거절했어요${d.note ? ` (${short(d.note, 60)})` : ''}` }, ts, rid); break;
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
    case 'request.questions': feed({ who: 'cso', text: `확인이 필요해요: ${short((d.questions || []).join(' / '), 150)}`, cls: 'alert' }, ts, rid); break;
    case 'request.step_done': { const q = req(rid); q.steps[d.step_id] = d.ok === false ? 'error' : 'done'; Object.assign(stepDetail(rid, d.step_id), { attempts: d.attempts || stepDetail(rid, d.step_id).attempts, error: d.reason || stepDetail(rid, d.step_id).error }); break; }
    case 'request.step_skipped': { const q = req(rid); q.steps[d.step_id] = 'skipped'; stepDetail(rid, d.step_id).error = d.reason || ''; break; }
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
      if (d.report) q.report = d.report;
      if (d.error) q.error = d.error;
      feed({ who: 'cso', text: q.status === 'done' ? '최종 보고서를 올렸어요' : `요청이 실패했어요: ${short(d.error, 100)}`, cls: q.status === 'done' ? '' : 'alert' }, ts, rid);
      break;
    }
    case 'github.posted': { const q = req(rid); q.github.push({ kind: d.kind, url: d.url, number: d.number }); feed({ who: 'github', text: GH_KO[d.kind] || 'GitHub에 업데이트했어요', url: d.url }, ts, rid); break; }
    case 'github.failed': feed({ who: 'github', text: `GitHub 업데이트 실패: ${short(d.error, 110)}`, cls: 'alert' }, ts, rid); break;
    default: break;
  }
  return effects;
}
function toolLabel(name) {
  const n = String(name || '').split('__').pop();
  return ({ hpc_submit: 'HPC 제출', hpc_status: 'HPC 확인', WebSearch: '웹 검색', WebFetch: '문헌 읽기', google_web_search: '웹 검색',
    Read: '파일 읽기', Glob: '파일 찾기', Grep: '코드 검색', Bash: '명령 실행', shell: '명령 실행', Edit: '파일 수정', Write: '파일 작성',
    Skill: '스킬 실행', Agent: '서브에이전트', edit: '파일 수정', web_search: '웹 검색' }[n]) || n;
}

return { S, apply, ag, visual, nick, req, setPlan, feed, toolLabel, STATE_KO, KIND_KO, JOB_KO, PHASES };
}
root.LabHQState = { createOfficeState };
})(globalThis);
