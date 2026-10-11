// Staff control in the staff sheet (#59 ①): change direction, pause/resume, halt. Shown while the staff member runs a
// task; for a task the PI halted (resume only); and, without halt, for a turn that ended into an HPC wait or a pause,
// whose next turn of the same session takes the request.
const RUNNING = new Set(['queued', 'working', 'waiting']);
export const CONTROL_LABELS = { steer: '방향 보내기', pause: '일시 정지', resume: '재개', halt: '멈추기' };

export function staffControl(agent, control = {}) {
  const taskId = agent?.taskId || '';
  const none = { taskId, status: '', actions: [] };
  if (!taskId) return none;
  if (control.halted) return { taskId, status: '멈춤 · 재개 가능', actions: ['resume'] };
  const status = control.paused ? '일시 정지됨' : '', toggle = control.paused ? 'resume' : 'pause';
  if (RUNNING.has(agent.state)) return { taskId, status, actions: ['steer', toggle, 'halt'] };
  if (agent.state === 'hibernating' || control.paused) return { taskId, status, actions: ['steer', toggle] };
  return none;
}

// One key per shape, so a live re-render does not wipe a direction the PI is typing.
export const controlKey = view => `${view.taskId}|${view.status}|${view.actions.join(',')}`;

export function controlHtml(view, esc) {
  if (!view.actions.length) return '';
  const steer = view.actions.includes('steer')
    ? `<textarea id="steer-text" rows="2" maxlength="2000" placeholder="방향 바꾸기: 지금 하는 일에 바로 반영할 지시"></textarea>` : '';
  const buttons = view.actions.map(act => `<button type="button" class="btn${act === 'halt' ? ' warn' : ''}" data-act="${act}">${CONTROL_LABELS[act]}</button>`);
  return `<div class="staff-control" data-task="${esc(view.taskId)}" data-key="${esc(controlKey(view))}">${steer}<div class="acts">${buttons.join('')}</div></div>`;
}
