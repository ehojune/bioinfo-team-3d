## 2026-10-03 · PR 준비 — 연구 결과 계약 교정 turn(#90, #298)

- 결론: 연구 단계 결과 JSON이 계약 검증에 걸리면 원 분석을 다시 돌리지 않고 한 번 고쳐 받아, 통과하면 요청을 계속한다.
- 바뀐 것: `research.result_corrections`(기본 1), 같은 session·workdir의 결과 교정 turn, validator 전용 필드 규칙 prompt를 추가했다. 일반 lane과 `evidence_checkpoint: false` 흐름은 그대로다.
- 실행한 것: 새 회귀 5건이 수정 전 실패했다. 수정 뒤 관련 pytest 365건, 공개 저장소 검사, diff 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/orchestrator/cso.py`, `labhq/evidence/claims.py`, `labhq/research/contract.py`, `labhq/settings.py`, `tests/test_research_cp2.py`, `tests/test_step_prompt_rules.py`.
