## 2026-10-07 · #470 — 계획의 pack·점검표 키 표기 정규화와 교정 안내 수정

- 결론: v0.5 두 번째 시운전(`req_bb9f96f460`)이 CP1 전에 실패한 원인을 고쳤다. CSO가 쓴 `bulk_tumor_normal`(판 없음)과 `microarray_expression.batch`(topic.id)를 검증 전에 정확한 키로 바꾼다. 틀린 pack 키의 교정 안내는 이제 topic이 적용한 pack을 기대값으로 보인다. 전에는 `[]`를 보여 CSO가 pack 값을 지웠다.
- 바뀐 것: `labhq/research/packs.py`(`normalize_pack_keys`, 선택 오류 문구), `labhq/vocab/topic_checklists.py`(`normalize_answers`), `labhq/orchestrator/cso.py`(`normalize_plan_keys`를 두 lane에 적용, 선택 예외 때 기대 pack), `docs/manual.md` 한 줄, `tests/test_research_bulk_pack.py` 3건.
- 실행한 것: 새 시험 중 2건은 수정 전 실패·수정 뒤 통과. 전체 pytest(Windows) 4471 passed·56 skipped. 실제 계획 4개(`req_cec10975ab` 1개, `req_bb9f96f460` 3개)로 원인 재현.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 재제출.
- 근거: `tests/test_research_bulk_pack.py::test_cso_spellings_of_pack_and_checklist_keys_reach_cp1_without_a_correction`, `::test_wrong_pack_key_correction_names_the_applied_pack_not_an_empty_snapshot`.
