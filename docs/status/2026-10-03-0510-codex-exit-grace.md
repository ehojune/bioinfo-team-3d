## 2026-10-03 · PR #332 (#330) — turn이 끝난 뒤 남은 직원 프로세스를 유예 뒤 정리

- 결론: 직원 CLI가 마지막 turn 이벤트를 낸 뒤 `runner.exit_grace_s`(기본 45초) 안에 끝나지 않으면 runner가 프로세스 트리를 끝내고, 이미 받은 결과로 단계를 마친다. 모의 시운전에서 Codex lit_scout가 turn을 끝내고도 `codex exec`가 남아 s3이 46분 넘게 `running`이었던 문제다.
- 바뀐 것: `labhq/adapters/base.py` `run()`에 exit guard. adapter가 `result_seen`을 켠 뒤 프로세스가 유예 안에 끝나지 않으면 `agent.log` warn 한 줄을 남기고 기존 `_kill`(Windows `taskkill /T /F`, POSIX 프로세스 그룹)로 정리한다. 이때 종료 코드는 0으로 보고 결과를 유지한다. Claude·Gemini·Antigravity·cli adapter도 같은 guard를 받고, 유예 안에 끝나는 정상 경로는 그대로다.
- Codex: `turn.failed`도 turn 종료로 본다. MCP stdio 서버 env 가운데 Codex 프로세스 env에 같은 값이 있는 키(broker URL·token·task id 등)는 `-c mcp_servers.<name>.env_vars=[…]`로 이름만 넘기고, 나머지(PYTHONPATH 등)만 `env={…}`에 남긴다. Codex는 이미 runner에게서 같은 env를 받으므로 새로 드러나는 곳은 없다.
- 실행한 것: 새 `tests/test_exit_grace.py` 6건(남는 fake Codex·Claude가 유예 1초 뒤 결과와 함께 끝나고 종료 로그 한 줄, 유예 안에 끝나는 fake와 exit 3 fake는 영향 없음, broker token이 argv에 없고 fake env에는 있음). 반영 전 3건 실패, 회귀 3건 통과를 확인했다. 관련 test 16개 파일 544 passed.
- 미해결: 프로세스는 끝났는데 손자 프로세스가 stdout을 잡고 있는 경우는 guard 대상이 아니다(`task_timeout_s`가 푼다). `env_vars`는 실제 Codex로 확인하지 않았다(codex.exe 안에 필드명은 있음). 실제 Codex로 MCP 호출 한 번을 돌려 봐야 한다.
- 근거: `labhq/adapters/base.py`, `labhq/adapters/codex.py`, `labhq/settings.py`, `tests/test_exit_grace.py`.
