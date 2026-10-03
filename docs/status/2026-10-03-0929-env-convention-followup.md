## 2026-10-03 · PR #344 후속 — 구조화된 결과의 PI 질문, 재계획의 환경 단계

- 결론: #344가 더한 PI 질문 text 탐색이 구조화된 결과를 낸 직원에게도 걸려, 로그 속 예시 JSON을 질문으로 읽을 수 있었다. 구조화된 결과가 없을 때만 text를 찾는다. 재계획 프롬프트에도 환경 단계 규칙을 넣었다.
- 바뀐 것: `blocking_question()`, `REPLAN_PROMPT`, 직원 공통 쓰기 규칙 문구("환경 단계가 있으면"), README §11 v0.5 행.
- 실행한 것: 관련 test 234건 통과, 새 test는 수정 전 실패.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/adapters/base.py`, `tests/test_cso.py`.
