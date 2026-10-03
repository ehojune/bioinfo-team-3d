## 2026-10-03 · Codex 직원 output schema strict 변환

- 결론: 전체 연구 schema에서 나던 `invalid_json_schema` 400을 고쳤다. 원본 계약과 hash는 그대로다.
- 바뀐 것: arbitrary-key 사전은 `{key,value}` 배열로 왕복한다. Codex가 거부한 type 제약은 transport schema에서 빼고, 중첩 nullable array는 `anyOf(array, null)`로 바꾼다. 응답은 원래 형태로 되돌린 뒤 기존 검증으로 보낸다.
- 실행한 것: 이분 탐색에서 type 제약과 `Claim.comparisons[].assumptions`의 nullable array 구조를 확인했다. 실제 gpt-5.6-luna로 STEP·pack 연구 계획·PLAN·REPLAN·REVIEW·연구 리뷰가 모두 기존 검증을 통과했고, 관련 pytest 109건과 공개 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/util.py`, `labhq/research/contract.py`, `tests/test_openai_strict_schema.py`, `tests/test_research_protocol.py`.
