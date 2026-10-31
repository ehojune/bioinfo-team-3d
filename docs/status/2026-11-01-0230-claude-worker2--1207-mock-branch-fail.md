# #1207 rev6: mock으로 갈래·단계 실패 경로를 확인한다

- 결론: `[branch-fail]`(분기 계획의 둘째 갈래 s2 실패)와 `[step-fail]`(다단계 계획의 s2 실패) 토큰을 더했다. 기본 init 설정 e2e로 실패 경로가 설계대로 도는지 단언했다. 제품 결함은 찾지 못했다.
- 확인된 동작(설계대로):
  - 분기: s1(b1)은 성공, s2(b2)는 실패(`mock step failure`), 종합 단계 s3은 그대로 실행됐다. s3 context에 `실패한 갈래: b2 — 이유: s2: mock step failure; 마지막으로 끝난 단계: none`이 들어간다.
  - 보고서는 s1·s3의 claim만 인용하고 s2의 claim은 인용하지 않는다. 실패는 보고서 한계에 `s2: 단계 실패 또는 의존 단계 건너뜀 — mock step failure`로, CP2 카드에 `경고 1건: 누락 칸·실패 단계는 한계로 남습니다`로 남는다.
  - 다단계: s1 성공 뒤 s2가 실패해도 같은 방식으로 CP2·리뷰·보고서까지 간다.
- 관찰(결함으로 단정하지 않음): 두 경우 모두 요청 상태는 `done`, outcome은 `research_reported`이고 요청 단위의 "일부 실패" 표지는 없다. 실패는 보고서 한계와 CP2 경고로만 보인다. 보고서의 labhq 한계 줄은 단계 id(s2)만 말하고 갈래(b2)는 말하지 않는다(갈래 이름은 종합 단계 context의 memo에 있다).
- 바뀐 것: `labhq/adapters/mock.py`(토큰·실패 반환), `tests/test_e2e_mock.py`(helper가 `hub`를 함께 돌려줌, 실패 시험 2건).
- 실행한 것: `test_e2e_mock`(11)·`test_demo_separation`·`test_research_cp2` 로컬 통과. 새 시험 2건은 옛 mock에서 실패함을 확인.
- 문서: manual 한·영의 mock 엔진 단락에 `[step-fail]`·`[branch-fail]`을 적었다(rev7).
- 일부 실패 요청이 `done`으로만 보이는 문제는 #1220으로 따로 다룬다.
- 미해결/미검증: 모든 갈래가 실패하는 경우, 갈래 안 여러 단계, 재시도는 확인하지 않았다.
