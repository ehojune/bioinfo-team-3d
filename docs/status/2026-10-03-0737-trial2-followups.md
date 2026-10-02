## 2026-10-03 · trial2-followups — 모의 시운전 F5와 직원 지침의 승인 카드 줄이기

- 결론: 리뷰가 상한 뒤에도 수정을 요구한 요청이 단계별 결과 나열 대신 CSO 최종 보고서를 남기고, 쓰기 직원이 임시 파일·변수 경로로 승인 카드를 띄우는 일을 줄인다.
- 바뀐 것: 미해결 리뷰도 합성을 돌리고 보고서에 "해결되지 않은 리뷰 지적" 절을 둔다. 요청은 failed와 `outcome=review_unresolved`를 유지하고, 재시작도 같은 경로를 탄다. 합성 실패나 예산 거부는 기존처럼 단계별 결과로 끝난다. 쓰기 직원 지침(`role_footer`)에 `.tmp/` 임시 폴더와 상대 경로 규칙 두 줄이 붙고 read-only task에는 붙지 않는다.
- 검증: 관련 test 4개 파일 452 passed, 1 skipped. 공개 저장소 검사 통과.
- 미해결: 합성 프롬프트가 리뷰를 3000자로 잘라 넘겨 그 너머의 지적은 CSO에게 보이지 않는다. 합성이 성공하면 보고서·이벤트에 "왜 failed인지"가 기계적으로 남지 않는다. `req["review"]`에는 전체가 있다.
- 근거: `labhq/orchestrator/cso.py`, `labhq/gateway/server.py`, `labhq/adapters/base.py`, `tests/test_cso.py`, `tests/test_state.py`, `tests/test_private_paths.py`, `tests/test_role_footer.py`.
