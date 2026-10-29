## 단계
#1045(#910 ② 2차): 엔진 CLI가 시작 직후 출력 없이 끝났을 때 원인을 가릴 근거가 없던 문제.

## 한 일
1. `labhq/adapters/base.py`: 결과 이벤트 없이 끝난 실행(`not ok`, `result_seen` 아님, labhq가 끝낸 것 아님)이면 작업 오류에 `[CLI 종료 증거: exit N, 초, stderr …, MCP 이름=상태]`를 붙이고, `agent.log` 이벤트와 `.labhq/exit_evidence.json`(exit_code·elapsed_s·stderr_tail 8줄·mcp)을 남긴다. labhq의 Job 안내 줄은 뺀다.
2. 기존 `empty CLI stream: …`·`exit N: …` 오류 문구의 stderr도 같은 정리(Job 줄 제외, `sanitize`로 비밀값·통제 구역 가림)를 거친다. 이전에는 원문이 그대로 실렸다.
3. `claude_code.py`: init 이벤트의 `mcp_servers`(name·status)를 `RunState.mcp_status`에 담는다. 다른 엔진은 MCP 상태를 알려 주지 않아 "MCP 시작 정보 없음"으로 적는다.
4. `tests/test_cli_exit_evidence.py`(즉시 종료 fake CLI, 비0 종료, 정상 경로 무변화, stderr 없음), manual 한·영, Windows inventory.

## 테스트 결과
- Windows `test_cli_exit_evidence`·`test_exit_grace`·`test_environment_signatures`·`test_real_streams`·`test_shutdown_*`·`test_isolation`·`test_e2e_mock` 등 327 passed·3 skipped, `windows_boundary`·inventory 188 passed.
- 미검증: 실제 ② 즉시 종료 재현(원인 자체는 이 변경이 다음 발생 때 가릴 수 있게 할 뿐 진단하지 않음), WSL.
