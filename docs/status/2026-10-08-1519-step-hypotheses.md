## 2026-10-08 · 연구 단계가 CP1 가설 원문을 받는다

- 결론: 연구 단계 prompt의 동결 요약(`frozen_context`)에 CP1 가설(주가설, null/대안, 구별할 관찰)이 없었다. 웹 연구 시운전 1차에서 s9가 A1·A2를 다시 정의해 리뷰 P1을 받았고, 2차에서는 "TASK.md에 가설 원문이 없다"며 PI에게 물으며 멈췄다. 이제 단계 prompt에 가설 원문이 들어가고, 가설은 그대로 쓰고 이름을 바꾸거나 합치지 말라는 지시가 붙는다.
- 바뀐 것:
  - `labhq/research/continuation.py` `frozen_context`: `hypotheses`(primary, null_or_alternatives, distinguishing_observations) 추가. 같은 요약이 이어 가기 재사용 기준이므로 가설이 바뀐 계획은 단계를 재사용하지 않는다(사유 "question, scope, hypotheses, protocol or pack values changed"). 재사용이 줄어드는 쪽으로만 바뀐다.
  - `labhq/orchestrator/cso.py`: 단계 prompt 머리말을 "Frozen question, hypotheses and protocol"로 바꾸고 가설 원문 사용 지시 한 문장.
  - 웹: 직원이 HPC job이 아니라 질문의 답을 기다리면 "답 기다리는 중"(하단 띠는 "답 기다림"). runner `agent.status`의 `jobs`·`asks`로 가른다. 페이지를 새로 열 때 `recent_events`에 계획 이벤트가 없어도 계획이 있는 진행 중 요청은 "실행"(리뷰가 accept면 "리뷰")부터 보인다. 시운전 2차 CP2에서 "브리핑"으로 보였다.
  - 문서: `docs/research_protocol.md` 동결 PLAN 계약, `docs/manual.md` 직원 상태 표.
- 실행한 것: 새 test가 main 코드에서 실패하고 이 branch에서 통과(`tests/test_research_cp2.py` 가설 원문, `tests/test_research_continue.py` 가설 변경 시 재사용 없음, `tests/web_cards_status.cjs` 답 대기 표시·새로 연 페이지 단계). 전체 `pytest -q`(Windows, 가설 커밋 기준) 4975 passed·63 skipped, web node test 전체, `tests/test_web.py`, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 이미 동결된 계획으로 도는 요청은 새 prompt를 다음 단계 dispatch부터 받는다(trial `req_d77e574f85` 2차는 PI 답으로 원문을 받았다).
- 근거: trial `req_d77e574f85` 1차 리뷰 P1(s9), 2차 직원 질문 카드 `appr_30672a3be3`.
