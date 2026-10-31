# #1170 후속: 늦은 step 재개·해제 이벤트도 끝난 요청에서 거절

- 결론: `request.step_resumed`·`request.step_quota_resumed`·`request.step_login_resumed`를 입구 규칙 목록(`PIPELINE_STARTS`)에 넣었다. 끝난 요청(review_pending 포함)의 완료된 step을 다시 working·pending으로 되돌리지 못한다. PR #1171 리뷰 P2.
- 바뀐 것: `labhq/web/state.js`(목록 3개), `tests/web_stale_events_1170.cjs`(6개: 5개 종료 상태 + 진행 중 요청에서 정상 동작 유지).
- 실행: 새 시험은 수정 전 5개 실패, 수정 후 통과. `web_stale_events_1170` 26개, `test_web_3d`·`test_web` 107 passed, 1 skipped.
- 미해결/미검증: `request.step_halt_expired`는 같은 handler를 쓰지만 step을 working으로 만들지 않고(halted일 때만 error로) 이번 지시 범위 밖이라 그대로 둔다. Argon P2(a52bc03 기준)는 같은 건인지 확인하지 못했다. 실제 브라우저 재연결은 못 봄.
- manual 한·영 웹 사무실 절에 "끝난 요청과 늦게 온 이벤트" 한 항목을 넣었다(문서 gate).
- Argon P2(늦은 `request.review` revise가 `q.steps`를 `revise`로 바꿈, a52bc03 기준)는 #1171 병합본의 입구 규칙에 `request.review`가 이미 있어 막혀 있다. 코드 변경 없이 종료 상태 5개 × issues 있는 입력 시험을 더해 고정했다(수정 전 실패 확인은 불가: 이미 막힌 경로).
- 시험: `web_stale_events_1170` 32개 통과, `test_web_3d`·`test_web` 107 passed.
