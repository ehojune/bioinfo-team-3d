# #1208 — WSL fallback 요청의 계획 prompt

- 결론: `format_capabilities`에 `run_where`를 `wsl_pinned`일 때만 넘기고, fallback이면 `run_where_note`(이유, 300자 이내)를 "WSL 없이 실행한다" 한 줄로 붙인다. 호출부 한 곳(`cso.py`).
- 시험: `tests/test_runner_pin_1204.py::test_the_plan_prompt_names_wsl_only_for_a_request_that_got_it_and_gives_the_reason_when_it_did_not`. 관련 298건 통과.
- 미검증: 실제 요청 흐름 전체(함수 단위 시험).
