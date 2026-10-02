## 2026-10-02 · PR #323 — consult refs와 read-only 후속(#86 #165)

- 결론: gateway는 runner 경로를 검사하지 않고, 같은 runner만 원래 작업 폴더를 받아 refs를 검증한다.
- 바뀐 것: 빈 refs 질의는 작업 폴더와 무관하게 보낸다. 다른 runner consult는 원래 workspace를 받지 않고 파일을 읽을 수 없다고 밝힌다.
- 실행한 것: 봇 P1 회귀가 수정 전 4건 실패·1건 skip, 수정 뒤 관련 pytest 141건 통과·2건 skip. 공개 검사를 통과했다.
- 해결됨: #165의 1~3번은 PR #216, 4번은 `.codex` fail-closed와 #148 종료 상태를 확인했다. 5번 실측 fixture를 추가했다.
- 미해결: CI와 재검토는 개발 총괄이 확인한다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/runner/daemon.py`, `labhq/runner/approvals.py`, `tests/test_ask.py`.
