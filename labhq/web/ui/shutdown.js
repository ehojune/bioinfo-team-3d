export const FINISHED = '끝났습니다. 이 탭을 닫아도 됩니다.';
export const TAB_ONLY = 'labhq는 계속 돌아갑니다. 나중에 다시 열면 이어서 볼 수 있습니다. 이 탭을 닫아도 됩니다.';
export const FORCE_WARNING = '진행 중인 일이 중단됩니다. 아직 저장하지 못한 보고서나 요청 묶음은 남지 않을 수 있습니다. 다시 켜면 재개 카드에서 이어 가세요.';

export function readinessText(data) {
  if (!data || typeof data.ready !== 'boolean') throw new Error('종료 상태를 확인하지 못했습니다.');
  const stuck = (data.finishing || []).filter(row => row.stuck);
  // A stuck bundle does not hold the shutdown back; the next start rebuilds it (#658).
  if (data.ready) return stuck.length ? `요청이 모두 끝났습니다
멈춘 마무리 ${stuck.length}건은 다음 시작 때 이어서 합니다` : '요청이 모두 끝났습니다';
  const requests = data.requests || [];
  const waiting = requests.filter(row => row.status === 'waiting_pi' || row.status === 'waiting_facilities_fix');
  const active = requests.filter(row => !waiting.includes(row));
  const title = row => String(row.text || row.id || row.request_id || '').slice(0, 100);
  const lines = [];
  if (active.length) lines.push(`진행 중인 요청 ${active.length}건 (${active.map(title).join(' / ')})`);
  if (waiting.length) lines.push(`PI 결정 대기 ${waiting.length}건 (${waiting.map(title).join(' / ')})`);
  if (data.followups?.length) lines.push(`이어 묻기 진행 중 ${data.followups.length}건 (${data.followups.map(title).join(' / ')})`);
  if (data.recruits) lines.push(`채용 진행 중 ${data.recruits}건`);
  const finishing = (data.finishing || []).filter(row => !row.stuck);
  const minutes = row => row.elapsed_s >= 60 ? ` ${Math.floor(row.elapsed_s / 60)}분째` : '';
  if (finishing.length) lines.push(`마무리 중 ${finishing.length}건 (${finishing.map(row => title(row) + minutes(row)).join(' / ')})`);
  if (stuck.length) lines.push(`멈춘 마무리 ${stuck.length}건은 다음 시작 때 이어서 합니다`);
  return lines.join('\n') || '아직 끝나지 않은 일이 있습니다.';
}

export function bindShutdown(button, {token, fetcher = (...args) => fetch(...args),
  doc = document, host = location.host, closeTab = () => window.close(),
  pause = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
  button.hidden = false;
  button.addEventListener('click', async () => {
    button.disabled = true;
    const dialog = doc.createElement('dialog');
    dialog.className = 'shutdown-dialog';
    dialog.setAttribute('aria-label', '종료하기');
    // No form submission or implicit Enter activation, including the destructive button itself.
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); }
    }, true);
    const title = doc.createElement('h2'); title.textContent = '정말 종료하시겠습니까?';
    const target = doc.createElement('p');
    target.textContent = `종료 대상: ${host}의 labhq입니다. 다른 PC에서 접속했다면 이 서버 PC의 인스턴스를 끕니다.`;
    const status = doc.createElement('p'); status.setAttribute('role', 'status');
    status.textContent = '진행 상태를 확인하고 있습니다.';
    const actions = doc.createElement('div'); actions.className = 'shutdown-actions';
    const add = (label, action) => {
      const el = doc.createElement('button'); el.type = 'button'; el.textContent = label;
      el.addEventListener('click', action); actions.append(el); return el;
    };
    const dismiss = () => { dialog.close(); };
    dialog.addEventListener('close', () => { dialog.remove(); button.disabled = false; button.focus(); });
    dialog.append(title, target, status, actions); doc.body.append(dialog); dialog.showModal();
    const cancel = add('돌아가기', dismiss); cancel.focus();
    add('탭만 닫기', () => {
      status.textContent = TAB_ONLY;
      actions.replaceChildren(); add('돌아가기', dismiss); add('이 탭 닫기', closeTab);
    });
    async function request(method, path, body) {
      const response = await fetcher(path, {method, cache: 'no-store',
        headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token()}`},
        ...(body ? {body: JSON.stringify(body)} : {}), signal: AbortSignal.timeout(5000)});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || '종료하지 못했습니다.');
      return data;
    }
    async function stop(force) {
      actions.replaceChildren(); status.textContent = 'labhq를 끄고 있습니다.';
      try {
        const accepted = await request('POST', '/api/shutdown', force ?
          {force: true, confirmation: 'interrupt-running-work'} : {force: false});
        if (accepted.status !== 'stopping') throw new Error('종료 요청을 확인하지 못했습니다.');
        let disconnected = 0;
        for (let i = 0; i < 60; i++) {
          await pause(500);
          let result;
          try { result = await request('GET', '/api/shutdown'); }
          catch (error) {
            // Only a transport disconnect after acceptance counts; HTTP/auth/JSON errors never do.
            if (!(error instanceof TypeError)) throw error;
            if (++disconnected < 2) continue;
            status.textContent = FINISHED;
            add('이 탭 닫기', closeTab); return;
          }
          disconnected = 0;
          if (result.status !== 'stopping') throw new Error('종료하지 못했습니다. 상태를 다시 확인해 주세요.');
        }
        throw new Error('종료를 확인하지 못했습니다. 잠시 뒤 상태를 다시 확인해 주세요.');
      } catch (error) { status.textContent = error.message; add('돌아가기', dismiss); }
    }
    try {
      const data = await request('GET', '/api/shutdown-readiness');
      // The user may have chosen tab-only or dismissed while the request was in flight.
      if (!dialog.open || status.textContent === TAB_ONLY) return;
      status.textContent = readinessText(data);
      const off = add('labhq 끄기', () => stop(false)); off.disabled = !data.ready;
      if (!data.ready) add('그래도 끄기', () => {
        status.textContent = `${readinessText(data)}\n\n${FORCE_WARNING}`;
        actions.replaceChildren(); add('돌아가기', dismiss).focus();
        add('진행 중인 일을 중단하고 labhq 끄기', () => stop(true));
      });
    } catch (error) { if (status.textContent !== TAB_ONLY) status.textContent = error.message; }
  });
}
