## 2026-10-12 · 러너를 다시 시작한 뒤 남는 도구 권한 카드를 닫는다 (#521)

- 결론: runner는 incarnation을 디스크에 두고 재시작해도 같은 값을 쓴다. 그래서 이전 프로세스가 띄운 카드가 R2 판정을 빠져나가 스냅샷에 계속 남았고, 승인해도 아무 일이 없었다. 이제 재시작과 재연결은 프로세스마다 새로 만드는 `process` 값으로 가린다. 카드를 띄운 task가 결과를 보내도 그 카드를 닫는다.
- 바뀐 것:
  - `runner.hello`에 `process`(Runner마다 uuid4, 저장하지 않음)를 싣는다. gateway는 runner 카드에 `origin_process`로 남기고 `_orphan_reason`에서 붙는 프로세스와 비교한다. incarnation은 지금처럼 task 세대 판정에 쓴다.
  - 호환: `process`가 없는 이전 러너끼리, 그리고 이 변경 전에 저장된 카드는 지금의 incarnation 규칙을 그대로 따른다.
  - 보강: 카드의 task가 `task.result`를 보내면 그 카드를 기존 expired 경로로 닫는다(note "그 작업이 끝나 닫힘"). 살아 있는 runner에도 거절이 가서 hook이 timeout까지 기다리지 않는다. 알림만 하는 `codex_sandbox_setup` 카드는 남긴다.
  - runner: 끝난 task의 카드에 늦게 온 답이 직원 상태를 `working`으로 되돌리지 않는다. daemon 변경은 4줄이다.
  - `docs/manual.md` R2 문단.
- 실행한 것: main(d083828c)에서 재현 스크립트로 확인했다. 같은 state로 Runner를 두 번 만들면 incarnation이 같고, 재등록 뒤 `expire_orphan_approvals`는 `[]`, `task.result` 뒤에도 카드가 남았다. 새 test 4개(같은 incarnation 재시작은 websocket 경유, 같은 프로세스 재연결 유지, task.result로 닫힘, 이전 러너 호환·업그레이드, 늦은 답의 상태)는 main 코드에서 4 failed, 이 branch에서 통과한다. Windows 전체 `pytest -q` 5020 passed·64 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check` 통과.
- 미해결: `recruits_running`도 같은 incarnation 문제를 안고 있다(#521 P2 11·18). 이번 `process` 값을 그대로 쓰면 고쳐지지만 이 PR 범위 밖이라 남긴다.
- 근거: `labhq/gateway/server.py`, `labhq/runner/daemon.py`, `tests/test_request_lifecycle.py`, `docs/manual.md`.
