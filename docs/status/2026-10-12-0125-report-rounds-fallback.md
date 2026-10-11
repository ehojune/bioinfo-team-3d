## 2026-10-12 · 이어 간 연구 보고서의 "이전 차수에서 고친 것" 절을 보장한다

- 결론: #512는 보고서 작성자에게 "이전 차수에서 고친 것" 절을 쓰라고 지시만 했고, 빠뜨려도 확인하지 않았다(#512 리뷰 P2). 이제 그 절이 없으면 labhq가 이전 차수 리뷰 P1과 CP2 수정 요청 목록으로 절을 본문에 붙인다. 절이 없다는 이유로 요청을 `report_incomplete`로 만들지는 않는다(claim 검사는 앵커만 본다).
- 바뀐 것: `labhq/orchestrator/cso.py` `EARLIER_ROUNDS_HEADING`, `_earlier_rounds_fallback`, `_research_report`에서 리뷰 참고 앞에 붙임. 작성자가 쓴 절(제목 단계 무관)이 있으면 그대로 둔다. `docs/manual.md` 한 줄.
- 실행한 것: `tests/test_research_continue.py` 종단 test 단언 추가와 새 test 1건이 main 코드에서 실패하고 이 branch에서 통과. 연구 test, 전체 `pytest -q`(Windows), `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음.
- 근거: #512 리뷰 인라인 지적(cso.py:3971), `tests/test_research_continue.py`.
