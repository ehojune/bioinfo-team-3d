## 2026-10-08 · 재개 카드가 멈춘 자리를 바르게 적는다

- 결론: 단계를 모두 마친 뒤(CP2·리뷰·이어 가기 카드·보고서 차례) 멈춘 요청의 재개 카드가 "남은 단계: 요청 전체" 대신 "없음(단계는 모두 끝났고 그 뒤 검토·결정·보고서부터 이어 갑니다)"으로 적힌다. 계획이 없는 요청은 지금처럼 "요청 전체"다.
- 바뀐 것: `labhq/gateway/server.py` `new_resume_approval`. 웹 `labhq/web/ui/decide.js`: 재개 카드 detail에서 요약과 겹치는 `request_text`를 숨기고, `created_at`은 접수 시각(YYYY-MM-DD HH:MM), `steps`는 남은 단계(쉼표 목록, 비면 "없음")로 보인다. `docs/manual.md` 재개 카드 문장.
- 실행한 것: 새 test 2건(`tests/test_request_lifecycle.py`, `tests/web_decision_detail.cjs`)이 main 코드에서 실패하고 이 branch에서 통과. `tests/test_request_lifecycle.py` 전체, web node test 전체, `scripts/check_public.sh`, `scripts/patch_notes.py check`. 실측: trial `req_d77e574f85`를 이어 가기 카드에서 재시작 → 재개 승인 → 같은 이어 가기 카드가 다시 뜨고 승인하면 2차로 넘어감.
- 미해결: 없음.
- 근거: trial `req_d77e574f85`(10-08 14:12 재시작), `tests/test_request_lifecycle.py::test_a_resume_card_after_every_step_finished_says_no_step_is_left`.
