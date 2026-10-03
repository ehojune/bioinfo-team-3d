## 2026-10-03 · 결과 계약 — 결합 문제를 첫 교정에

- 결론: 9차 모의 시운전이 `research_failed`로 끝났다. biologist 단계(s05)가 근거 행 id를 슬롯 이름으로 붙이고 `slots` 필드를 빠뜨렸는데, 결합 검사가 필드 검증 통과 뒤에만 돌아 슬롯 누락이 교정 2회가 끝난 뒤에야 드러났다. 하류 6단계가 skip됐다.
- 바뀐 것: `labhq/research/contract.py` — `research_result_errors`가 필드 오류가 있어도 원본 JSON으로 claim·슬롯 결합 문제를 함께 낸다(`_raw_binding_errors`). 슬롯 이름을 단 행이 `slots`를 안 적었으면 `add "slots": ["<slot>"]`를 덧붙인다. salvage는 그대로다(필수 슬롯 누락은 건지지 않음).
- 실행한 것: 전체 test 3534 passed. 새 test 2건은 수정 전 실패.
- 미해결: 10차 시운전으로 확인.
- 근거: `tests/test_research_cp2.py::test_a_missing_slot_is_reported_with_the_field_errors_and_names_the_fix`, `::test_the_first_correction_asks_for_the_slot_with_the_field_error`.
