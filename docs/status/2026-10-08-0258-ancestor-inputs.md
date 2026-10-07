## 2026-10-08 · #480 — 단계에 간접 조상 단계의 산출도 inputs/로 연결

- 결론: 단계의 입력 링크와 읽기 폴더를 직접 의존 단계에서 모든 조상 단계로 넓혔다. v0.5 시운전 `req_8433789745`의 전처리 단계가 간접 조상(fetch)의 산출을 못 찾아 실패한 원인이다.
- 바뀐 것: `labhq/orchestrator/cso.py`(`input_steps`, `upstream_steps`·`upstream_dirs`, 간접 조상 파일 목록 맥락), `docs/manual.md` 한 문장, `tests/test_cso.py` 1건.
- 실행한 것: 새 시험 수정 전 실패·수정 뒤 통과, 전체 pytest(Windows) 4669 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 6차.
- 근거: `tests/test_cso.py::test_a_step_gets_inputs_links_for_ancestors_it_reaches_through_other_steps`.
