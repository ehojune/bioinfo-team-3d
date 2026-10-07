## 2026-10-08 · #478 — 결과 원장 salvage가 설명 조각과 실패 조회 링크에서 포기하지 않게 함

- 결론: v0.5 시운전 `req_ce7c64089c`의 s10 annotation 원장이 교정 두 번 뒤 salvage까지 실패해 요청 전체가 끝났다. salvage는 이제 행 참조 없는 설명 조각을 건너뛰고, 실패·0건 조회를 근거로 단 링크만 거부한다. 실제 원장은 문제 행 하나와 그 링크만 빠지고 claim 둘이 남는다.
- 바뀐 것: `labhq/research/contract.py`(`_names_a_row`, 설명 조각 규칙, 실패 조회 링크 대상), `labhq/evidence/claims.py`(0건 observed 안내), `docs/manual.md` 연구 절 한 문장, `tests/test_research_cp2.py` 3건.
- 실행한 것: 새 시험 중 2건은 수정 전 실패·수정 뒤 통과, 실제 원장 재현, 전체 pytest(Windows) 4665 passed·57 skipped, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 병합 뒤 인스턴스 재시작, 시운전 5차.
- 근거: `tests/test_research_cp2.py::test_a_failed_lookup_linked_as_support_is_salvaged_by_refusing_only_the_link`.
