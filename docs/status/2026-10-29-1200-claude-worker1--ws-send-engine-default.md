## 단계
#1001·#1004 리뷰의 비차단 지적 2건 정리.

## 한 일
1. `tests/web_office_browser.cjs` `cdp()`: `ws.send`가 동기 예외를 내면(소켓이 이미 닫힘) 그 호출의 30초 timer와 `pending` 항목을 지우고 같은 오류로 reject합니다. 이전에는 Promise 실행기에서 예외가 reject로만 이어지고 timer가 남았습니다.
2. `labhq/gateway/server.py` `Hub._agent_mcp`: `agent.get("engine", "claude_code")`로 읽어 `engine` 키가 없는 직원 dict에도 `labhq_job`을 표시합니다(AgentSpec 기본값 `claude_code`와 같음). 엔진이 `codex` 등으로 명시된 직원은 그대로 제외됩니다.

## 테스트 결과
- 새 시험 `test_an_agent_record_without_an_engine_key_is_a_claude_agent_for_the_job_tool`(engine 키 없음→표시, codex→제외, `job` 없음→없음, AgentSpec 기본값 확인). `test_job_tool_engines_1003.py`·`test_local_jobs_675.py` 46 passed, 1 skipped.
- 실제 Chrome으로 `web_office_browser.cjs` 직접 실행: 두 화면 측정과 정리까지 끝남(약 19.5초).
- 미검증: 1번이 실제로 프로세스 종료를 앞당기는지. `ws.send`가 항상 던지게 만든 임시 사본으로 수정 전후를 재 보았으나 둘 다 0.5초에 끝나 지연 차이를 재현하지 못했습니다(node:test가 끝나면 남은 timer와 무관하게 종료). 따라서 1번은 정리 성격의 변경이며 효과를 입증하지 못했습니다.

## 바꾼 파일
tests/web_office_browser.cjs, labhq/gateway/server.py, tests/test_job_tool_engines_1003.py와 변경 기록.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
