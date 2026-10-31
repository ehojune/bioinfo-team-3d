# #1207: mock 엔진이 기본 init 설정(research 켜짐)에서 요청을 끝까지 돌린다

- 결론: mock CSO가 research 계약을 통과하는 계획을 내고, 단계는 결과 ledger와 산출 파일을, 리뷰어는 accept를, 보고서는 claim anchor를 낸다. `labhq init`이 쓰는 예시 설정으로 CSO 경유 요청이 계획 → CP1 → 단계 → CP2 → 리뷰 → 보고서까지 `done`으로 끝난다.
- 바뀐 것: `labhq/adapters/mock.py`(research 계획·단계 결과·리뷰·보고서 분기, 단계 전에 runner의 출력 기준선 호출), `tests/test_e2e_mock.py`(기본 init 설정 e2e 1건 추가).
- 숨은 mock 결함 하나: mock이 `before_spawn`을 부르지 않아 단계 산출 파일이 hash 없이 collected되어 CP2의 claim 검사가 `no recorded artifact_sha256`로 막혔다. 호출을 더해 해결.
- 판정(고치지 않음): 단계 전에 뜨는 `result_storage` 카드와 계획 3번 재실행은 서로 다른 이유다.
  - 카드는 **제품 동작**이다. 계획이 나온 뒤 CP1 카드 앞에 한 번 뜬다(순서: briefing, precedent, plan, result_storage, research_plan, step, research_evidence, review, synthesis). 계획은 한 번만 돈다.
  - 계획 3번 재실행은 **mock 탓**이다. 옛 mock 계획이 research 계약 검사에 32건 걸려 CSO 보정이 두 번 더 돈 것이다. 유효한 계획에서는 한 번만 돈다(새 시험이 순서로 고정).
- 실행한 것: `test_e2e_mock`(6, 새 시험 포함)·`test_demo_separation`·`test_research_cp2`·`test_intake_questions`·`test_clean_install_check` 160 passed. 새 시험은 옛 mock에서 계획 검증 실패로 실패함을 확인.
- 미해결/미검증: 시험은 Windows runner에서 restricted 데이터 구역을 뺀 설정으로 돈다(init도 Windows에서 그렇게 둔다). 다단계·branch·pack 적용 research 계획은 mock이 내지 않는다.
