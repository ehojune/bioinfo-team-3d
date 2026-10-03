## 2026-10-03 · #346 후속 — 범위 판정 보존, bench, CI

- 결론: 뒤 계획에 범위 판정이 없으면 첫 판정을 쓰고, bench는 범위 카드를 진행으로 답하며, 범위 카드 Node test를 CI에서 돌린다.
- 바뀐 것: `labhq/orchestrator/cso.py`(`scope_first`), `labhq/bench.py`, `tests/test_web_3d.py`.
- 실행한 것: 관련 test 87건 통과, 새 test 2건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_scope_gate.py`.
