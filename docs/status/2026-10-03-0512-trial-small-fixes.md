## 2026-10-03 · #331 — 모의 시운전 작은 결함 네 건

- 결론: 시운전 1차에서 나온 작은 결함 네 건(ask 500, 실패 보고서 크기, 접근 로그 서식 오류, 보기 표시 중복)을 고쳤다.
- 바뀐 것:
  - `labhq_ask`의 `wait`가 MCP schema에서 `short`·`hibernate` enum이 됐다. runner broker는 `/ask`·`/approval` 본문 검증에 실패하면 500 대신 400과 `fix and call again: <필드>: <이유>`를 돌려주고, ask 도구는 이것을 연결 실패가 아닌 rejected 답으로 전한다.
  - 실패한 요청의 보고서는 `report_results`가 따로 만든다. 성공 단계는 결과 요약 1,200자와 산출 경로(10개까지), 실패 단계는 원인 한 줄·뿌리 원인(3개까지)·`Next:`만 싣는다. 지시문은 160자로 자른다. reviewer·synthesis 입력(`format_results`)은 그대로다.
  - 로그 token 가림 필터는 인자별로 가려도 같은 줄이 나오면 `args`를 남긴다. uvicorn `AccessFormatter`가 인자 다섯 개를 그대로 받는다. token이 서식과 인자에 걸치거나 예외가 붙은 레코드만 전처럼 한 줄로 편다.
  - 보기 글 앞의 `a)`·`(a)` 표시는 `normalize_questions`·`ClarifyingQuestion`과 결정함 `decide.js`에서 뗀다. `A. thaliana`처럼 괄호 없는 글은 그대로 둔다.
- 실행한 것: 새 test 7건(ask 400 세 경우, MCP schema·400 전달, 12단계 실패 보고서, 접근 로그 서식, 보기 중복 Python·Node)이 수정 전 실패하고 수정 뒤 통과했다. 12단계 실패 test 보고서는 666,732자에서 11,152자가 됐다. 관련 test 파일 36개 1,202 passed·16 skipped, Node 3개 통과.
- 미해결: 보고서의 `Step status and output paths` 절은 산출 경로 수를 자르지 않는다(원인 문구만 200자로 자른다).
- 근거: `labhq/runner/approvals.py`, `labhq/tools/ask_mcp.py`, `labhq/orchestrator/cso.py`, `labhq/security.py`, `labhq/intake.py`, `labhq/web/ui/decide.js`, `tests/test_ask.py`, `tests/test_cso.py`, `tests/test_security.py`, `tests/test_intake_questions.py`, `tests/web_clarify_options.cjs`.
