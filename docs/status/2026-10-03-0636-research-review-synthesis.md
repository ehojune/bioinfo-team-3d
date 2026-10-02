## 2026-10-03 · PR 준비 — 연구 lane CP2 뒤 리뷰·보고서 완주와 claim 앵커 검사(#90, #58 ③⑤)

- 결론: CP2에서 승인하면 리뷰 한 번 → CSO 보고서 → claim 앵커 검사까지 간다. 결과는 `research_reported`, `report_incomplete`, `research_review_revise`, `research_review_unparsed` 중 하나로 끝난다.
- 바뀐 것: 연구 전용 리뷰 스키마 `RESEARCH_LANE_REVIEW_SCHEMA`와 리뷰·합성 프롬프트를 더했다. 앵커 검사는 `labhq/evidence/report_check.py`, 결과는 `research_contract.report_check`, 리뷰는 `research_contract.review`(재시작 뒤 재사용)에 남는다. 실패한 조회는 보고서 메타데이터에 따로 붙는다. generic 리뷰·합성과 `evidence_checkpoint` off의 프롬프트는 그대로다.
- 실행한 것: 새 test 15건 중 수정 전에 돌린 14건이 모두 실패했고, 수정 뒤 15건 모두 통과했다. 관련 pytest 416 passed, 공개 저장소 검사 통과.
- 미해결: `reviewer_agent`가 없으면 리뷰·보고서 없이 `evidence_approved`로 끝난다. 웹 피드는 연구 리뷰 이벤트를 따로 받지 않는다(웹 변경은 범위 밖).
- 근거: `labhq/orchestrator/cso.py`, `labhq/evidence/report_check.py`, `labhq/research/packs.py`, `tests/test_research_report.py`, `tests/test_report_check.py`.
