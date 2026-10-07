## 2026-10-08 · #487 — 연구 단계 근거가 수집 안 된 파일을 인용하면 CP2 전에 한 번 교정 (#485)

- 결론: v0.5 시운전 8차(req_7ccde78be0)에서 qc_data 판정 근거가 계획에 없는 `outputs/qc/data_qc_checks.json`을 인용했다. 이 파일은 수집되지 않아 CP2에서 거부됐고, claim은 unsupported가 됐다. 리뷰어도 같은 점을 P2로 짚었다. 이제 단계 결과 교정 루프에서 원장이 계약을 통과한 뒤 CP2와 같은 결속 검사를 한 번 돌린다. 거부될 근거가 있으면 수집 산출 목록을 주고 인용을 고치게 한다. 이 교정이 실패하거나, 수집 안 된 파일만 새로 쓰거나, 인용을 그대로 두면 단계는 교정 전 결과로 통과하고 CP2가 지금처럼 거부한다. 교정 질문은 단계당 한 번이다. 미보고 산출 목록에서 `__pycache__` 아래 파일은 뺀다(관찰 기록에는 남음).
- 바뀐 것: `labhq/orchestrator/cso.py`(`ancestor_artifacts`로 CP2와 같은 조상 산출 목록, `unbound_evidence_problems`, 교정 루프의 `bindable` 폴백), `labhq/runner/daemon.py`(미보고 산출에서 `__pycache__` 제외), `docs/manual.md` 연구 lane 절, 시험 `tests/test_research_cp2.py` 4건·`tests/test_observed_outputs.py` 1건, `tests/test_research_report.py`는 합성 호출을 순서 대신 kind로 찾음.
- 실행한 것: 새 시험이 고치기 전 코드에서 실패, 고친 뒤 통과. 전체 pytest(Windows) 4736 passed·59 skipped. `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: `tests/test_research_cp2.py::test_a_citation_of_an_uncollected_file_gets_a_correction_before_cp2`, `::test_a_binding_correction_never_fails_a_step_that_passed`.
