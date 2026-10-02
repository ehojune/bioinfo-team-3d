## 2026-10-02 · consult refs와 read-only 후속(#86 #165)

- 결론: ask의 원래 작업 폴더와 그 안에서 검증된 refs만 consult가 읽게 했다.
- 바뀐 것: broker가 작업 폴더를 증명하고, gateway가 refs의 실경로를 확인해 read-only consult 입력으로 넘긴다.
- 실행한 것: 수정 전 회귀 2건 실패, 수정 후 관련 pytest 313건 통과·18건 skip, Claude 2.1.282 실측과 공개 검사를 통과했다.
- 해결됨: #165의 1~3번은 PR #216, 4번은 `.codex` fail-closed와 #148 종료 상태를 확인했다. 5번 실측 fixture를 추가했다.
- 미해결: CI와 봇 리뷰는 개발 총괄이 확인한다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/approvals.py`, `tests/test_ask.py`, `tests/fixtures/real/claude_code/claude_hidden_memory_excludes.json`.
