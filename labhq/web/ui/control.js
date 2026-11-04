// Staff control in the staff sheet (#59 ①): change direction, pause/resume, halt. One row per task, so a staff member
// running two tasks at once is steered on the task the PI picks (PR #532 review). A row shows while its task runs; for
// a task the PI halted (resume only); and, without halt, for a turn that ended into an HPC wait or a pause, whose next
// turn of the same session takes the request.
const RUNNING = new Set(['queued', 'working', 'waiting']);
// A plan step's turn or a direct request's turn; consults, follow-ups, reviews and the rest take no control.
const CONTROLLABLE = new Set(['step', 'direct']);
export const CONTROL_LABELS = { steer: '방향 보내기', pause: '일시 정지', resume: '재개', halt: '멈추기', release_breaker: '제한 해제' };

// task: {taskId, state, title}; control: that task's agent.control state.
export function staffControl(task, control = {}) {
  const taskId = task?.taskId || '', title = task?.title || '';
  const none = { taskId, title, status: '', actions: [] };
  if (!taskId || (task.kind && !CONTROLLABLE.has(task.kind))) return none;
  if (control.halted) return { taskId, title, status: '멈춤 · 재개 가능', actions: ['resume'] };
  const status = control.paused ? '일시 정지됨' : '', toggle = control.paused ? 'resume' : 'pause';
  // The runaway breaker (#59 ②) limited this task to read-only tools; only the PI lifts it.
  const lifted = control.constrained ? ['release_breaker'] : [], limited = control.constrained ? [status, '도구 제한 중'].filter(Boolean).join(' · ') : status;
  if (RUNNING.has(task.state)) return { taskId, title, status: limited, actions: ['steer', toggle, 'halt', ...lifted] };
  if (task.state === 'hibernating' || control.paused) return { taskId, title, status, actions: ['steer', toggle] };
  return none;
}

// Every task of the staff member that has a control row. `agent.tasks` maps task id -> {state, title} (state.js).
export function staffControls(agent, controlOf = () => ({})) {
  const rows = [];
  for (const [taskId, task] of agent?.tasks || []) {
    const view = staffControl({ ...task, taskId }, controlOf(taskId) || {});
    if (view.actions.length) rows.push(view);
  }
  return rows;
}

// One key per shape, so a live re-render does not wipe a direction the PI is typing.
export const controlKey = views => views.map(v => `${v.taskId}|${v.status}|${v.actions.join(',')}`).join(';');

export function controlHtml(views, esc) {
  return views.map(view => {
    const head = `<p class="control-head">${esc(view.title || view.taskId)}${view.status ? ` · ${esc(view.status)}` : ''}</p>`;
    const steer = view.actions.includes('steer')
      ? `<textarea class="steer-text" rows="2" maxlength="2000" placeholder="방향 바꾸기: 지금 하는 일에 바로 반영할 지시"></textarea>` : '';
    const buttons = view.actions.map(act => `<button type="button" class="btn${act === 'halt' ? ' warn' : ''}" data-act="${act}">${CONTROL_LABELS[act]}</button>`);
    return `<div class="staff-control" data-task="${esc(view.taskId)}">${head}${steer}<div class="acts">${buttons.join('')}</div></div>`;
  }).join('');
}
