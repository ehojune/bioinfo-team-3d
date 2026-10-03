## 2026-10-03 · CP2 근거 결합 — 조상 단계 산출물 인정

- 결론: 8차 모의 시운전 CP2에서 거부된 근거 8건 중 6건은 해석·보고 단계가 조상(직접 상류가 아닌) 단계의 선언·hash된 산출물을 인용한 경우였다. 결합 규칙을 조상 전체로 넓혔다.
- 바뀐 것: `labhq/orchestrator/cso.py` — `step_ancestors()`(run_dag의 조상 계산을 함수로 뺌), CP2 결합의 upstream을 조상 전체로. 계획 사슬 밖 단계는 여전히 거부한다.
- 실행한 것: 전체 test 3530 passed. 새 test는 수정 전 실패(조부모 인용이 거부됨).
- 미해결: 상류 단계의 미선언 파일 인용(나머지 2건)은 설계대로 거부한다.
- 근거: `tests/test_research_cp2.py::test_cp2_binds_an_ancestors_output_but_not_an_unrelated_steps`.
