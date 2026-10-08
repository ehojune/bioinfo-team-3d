## 2026-10-08 · 이어 가기 카드가 리뷰 P1 지적을 읽을 수 있게 보인다

- 결론: 리뷰 revise 뒤 뜨는 이어 가기 카드(`research_continue`)가 P1 지적을 단계·문제·고칠 점 목록으로 펼쳐 보인다. 전에는 JSON 원문(시운전에서 3,492자)이 접힌 채로만 있었다.
- 바뀐 것: `labhq/web/ui/decide.js` `renderReviewIssues`. 지적 목록이 맨 위에 펼쳐지고, 원문 JSON은 그 아래 접혀 남는다. 내부 이름 `gate`는 숨기고 `round`·`limit`·`plan_sha256`은 이어 가기 차수·상한·지난 계획 plan hash로 보인다. `docs/manual.md` 리뷰·이어 가기 절 한 줄.
- 실행한 것: 새 node test 1건이 main 코드에서 실패하고 이 branch에서 통과(`tests/web_decision_detail.cjs`). web node test 전체, `tests/test_web.py`, `scripts/check_public.sh`, `scripts/patch_notes.py check`.
- 미해결: 없음. 카드 안 "승인하면 CSO가 새 계획을 쓰고 새 CP1을 받습니다" 문구는 그대로다.
- 근거: 웹 연구 시운전 trial `req_d77e574f85`(GSE10072) 리뷰 revise 카드, `tests/web_decision_detail.cjs`.
