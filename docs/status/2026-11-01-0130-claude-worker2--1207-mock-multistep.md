# #1207 rev3: mock이 다단계·분기 연구 계획을 낸다

- 결론: 요청 문안에 `[multistep]`을 넣으면 s2가 s1에 의존하는 2단계, `[branched]`를 넣으면 두 갈래(b1·b2)와 둘을 읽는 synthesis 단계(s3)를 가진 research 계획을 낸다. 둘 다 `labhq init` 예시 설정에서 CP1 → 단계 → CP2 → 리뷰 → 보고서까지 `done`으로 끝난다. 문안에 토큰이 없으면 이전처럼 1단계다.
- 바뀐 것: `labhq/adapters/mock.py`(`_research_shape`·`_research_step`, `before_spawn`의 거부 사유를 `TaskResult(ok=False)`로 돌려줌), `tests/test_e2e_mock.py`(기본 init 설정 e2e를 공용 helper로 묶고 다단계·분기 시험 추가, 거부 시험 추가).
- 이전 PR #1211의 P2: mock이 `before_spawn()`의 거부 사유를 버리던 것을 고쳤다. 거부되면 실행·산출 쓰기 전에 실패한다.
- 실행한 것: `test_e2e_mock`·`test_demo_separation`·`test_demo_phone`·`test_research_cp2` 123 passed. 새 시험 3건은 옛 mock에서 실패함을 확인.
- 미해결/미검증: 단계는 모두 한 작업자·한 산출 파일이다. pack이 적용되는 주제, 단계마다 다른 작업자, 분기 중 실패한 갈래는 mock이 내지 않는다.
