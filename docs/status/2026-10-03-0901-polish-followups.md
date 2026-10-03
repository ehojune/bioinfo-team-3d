## 2026-10-03 · #84·#57 — 다듬기 후속: PI 질문 줄바꿈·질문 길이 규칙·결정 카드 줄바꿈

- 결론: #342 봇 지적 두 건과 pre-trial-polish 메모의 미해결 세 건을 닫았다. 일반 lane 직원이 PI 질문 JSON 문자열 안에 실제 줄바꿈을 써도 질문이 사라지지 않고, 재계획·연구 계획 프롬프트에도 질문 길이 규칙이 들어갔다. 결정 카드는 summary 줄바꿈을 그대로 보여 준다.
- 바뀐 것: `extract_json`에 `strict=True` 인자를 더했다. 기본값은 그대로라 다른 호출자는 엄격하고, `blocking_question`만 `strict=False`로 읽는다. STEP_PROMPT에 줄바꿈은 `\n`으로 쓰라는 문장을 더했다. `PI_CARD_QUESTION_RULE` 상수를 PLAN_PROMPT와 REPLAN_PROMPT가 함께 쓴다(PLAN_PROMPT 바이트는 그대로). 연구 계획 프롬프트의 질문 줄에 500자 이하·질문 먼저를 더했고 `RESEARCH_PROMPT_SHA`를 의도한 변경으로 갱신했다. 2.5D `.ap .sum`과 3D 패널에 `white-space:pre-wrap`을 넣었다. CI가 돌리지 않던 Node test 8개를 `tests/test_web_3d.py` 목록에 등록했다.
- 검증: `test_step_prompt_rules.py`·`test_output_types_research.py`·`test_web_3d.py` 61 passed 1 skipped, `tests/web_decision_summary.cjs`(신규) fail 0, `scripts/check_public.sh` 통과. 전체 pytest는 CI에 맡겼다.
- 미해결: `strict=False`는 마지막 "가장 큰 객체" 탐색에서 날 줄바꿈이 든 관련 없는 큰 객체도 후보로 받아, 펜스 없는 텍스트에 그런 객체와 정상 escape된 blocking JSON이 같이 있으면 질문을 놓친다. 더 큰 객체가 이기는 부류는 main에도 있었다. `blocking_question`이 `blocking_decision` 키를 가진 후보를 먼저 고르면 막힌다.
- 근거: `labhq/util.py`, `labhq/orchestrator/cso.py`, `labhq/web/index.html`, `labhq/web/lab3d/index.html`, `tests/test_step_prompt_rules.py`, `tests/test_output_types_research.py`, `tests/test_web_3d.py`, `tests/web_decision_summary.cjs`.
