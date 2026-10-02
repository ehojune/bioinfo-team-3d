## 2026-10-03 · #328 — Codex 판본이 바뀌면 직원 sandbox 준비를 다시 확인

- 결론: 직원 `CODEX_HOME`의 elevated sandbox 준비가 지금 쓰는 Codex 판본에서 확인됐는지 labhq가 기록하고 비교한다. 10-03 00:27 앱 업데이트 뒤 marker 파일만 보던 doctor는 ok였지만 engineer 단계는 `ELEVATED_SETUP_ERROR`로 멈췄다.
- 바뀐 것: `labhq/runner/codex_sandbox.py`를 새로 두었다. Windows elevated 실행이 성공하면 runner가 `CODEX_HOME/.labhq-sandbox-ok.json`에 판본·실행 파일 표시·시각을 쓴다. 판본은 runner 프로세스마다 한 번 읽는다. doctor는 `setup_marker.json`이 있는 home마다 `codex sandbox version` 행을 낸다. 기록과 현재 `codex --version`이 같으면 ok, 다르거나 기록이 없으면 warn과 PowerShell 명령(`CODEX_HOME`, `$env:TEMP` 아래 probe 폴더, `$env:LOCALAPPDATA` 기준 codex.exe, `exec -s workspace-write -c 'windows.sandbox="elevated"'`, `Test-Path`)을 보인다. `labhq init`도 Windows에서 로그인 명령 뒤에 같은 명령을 출력한다.
- 알림: 준비 오류(UAC helper 취소, 또는 Codex의 "sandbox setup required" 메시지)로 단계가 실패하면 runner마다 한 번만 `agent.log` alert와 결정함 카드(`codex_sandbox_setup`, 승인·거절 어느 쪽이든 닫기만 함)를 낸다. alert는 피드에만 떠서 결정함 카드를 함께 쓴다.
- `bin: auto`: codex.exe가 있는 앱 폴더만 후보로 보고, 각 `codex.exe --version`(바이너리마다 한 번)이 모두 읽히면 판본이 가장 높은 곳, 하나라도 못 읽으면 가장 최근 폴더를 고른다. doctor engine 행에 고른 폴더와 이유를 적는다.
- 실행한 것: 새 test 14건(`tests/test_codex_sandbox_version.py`) 중 새 모듈만 둔 main 코드에서 doctor·auto·스트림·runner 연결 10건이 실패하고 이 branch에서 모두 통과했다. 관련 test 파일 20개 750 passed, `tests/web_state.cjs` 통과. 실제 Codex CLI는 돌리지 않았다(fake만).
- 미해결: Codex가 sandbox 준비 유효성을 무인으로 판정하는 공식 방법은 찾지 못해 판본 기록으로 대신했다. PI가 명령으로 다시 준비한 뒤에도 다음 Codex 직원 작업이 성공할 때까지 doctor는 warn이다.
- 근거: `labhq/runner/codex_sandbox.py`, `labhq/adapters/base.py`, `labhq/doctor.py`, `tests/test_codex_sandbox_version.py`.
