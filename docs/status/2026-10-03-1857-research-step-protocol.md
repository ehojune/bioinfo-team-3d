## 2026-10-03 · 연구 단계 — 동결 protocol을 단계 프롬프트에

- 결론: 8차 모의 시운전 리뷰가 P1로 revise했다. DE 단계가 동결 protocol과 다른 저발현 필터를 쓰고(양성 대조 NEK2·TTK 탈락) 이를 적지 않았다. 원인은 단계 프롬프트에 protocol이 들어가지 않은 것이다. 지시는 "저발현 필터 뒤"뿐이었다.
- 바뀐 것: `labhq/orchestrator/cso.py` `_research_protocol_digest()` — 연구 단계 프롬프트에 질문·범위·protocol·pack 값을 넣는다(6000자 제한). 이탈하면 method_changes에 적고 사전 규칙을 지켰다고 쓰지 않게 한다.
- 실행한 것: 전체 test 3530 passed. 새 test는 수정 전 실패.
- 미해결: 9차 시운전으로 리뷰 통과 → claim 앵커 보고서까지 확인.
- 근거: `tests/test_research_cp2.py::test_research_step_prompt_carries_the_frozen_protocol`.
