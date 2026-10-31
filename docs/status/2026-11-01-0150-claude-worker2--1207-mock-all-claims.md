# #1207 rev5: mock 보고서가 모든 단계의 claim을 싣는다

- 결론: PR #1214 리뷰 P2를 고쳤다. mock synthesis가 `anchors[0]`만 쓰던 것을 모든 claim anchor(중복 제거)마다 한 문장으로 바꿨다. 다단계·분기 계획에서 둘째 이후 단계와 갈래, 종합 단계의 claim이 보고서에 빠지지 않는다.
- 바뀐 것: `labhq/adapters/mock.py`(synthesis 보고서), `tests/test_e2e_mock.py`(`_every_claim_is_cited`: 계획의 모든 단계·claim_id의 anchor가 보고서에 있는지 단언, `or` 제거, 분기 시험에도 적용).
- 실행한 것: `test_e2e_mock`·`test_demo_separation`·`test_research_cp2` 101 passed. 다단계·분기 시험 2건은 옛 mock에서 실패함을 확인.
- 미해결/미검증: pack이 적용되는 주제, 단계마다 다른 작업자, 분기 중 실패한 갈래는 mock이 내지 않는다.
