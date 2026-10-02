## 2026-10-02 · #199 #203 — 승인 시간 초과 정리와 stale 알림 범위

- 결론: 러너에서 만료된 승인은 gateway와 화면에서도 즉시 끝나며, stale 알림은 누른 화면에만 보인다.
- 바뀐 것: broker가 `approval.timed_out`을 보내면 gateway가 승인 기록을 지우고 `approval.resolved(state=timed_out)`를 배포한다. `approval.stale`은 해당 WebSocket에 직접 보낸다.
- 실행한 것: 수정 전 회귀 test 2건 실패 확인. 수정 뒤 관련 pytest 287건 통과, 1건 skip. 공개 검사와 diff 검사 통과.
- 미해결: CI와 봇 리뷰는 개발 총괄이 이어서 확인한다.
- 근거: `labhq/runner/approvals.py`, `labhq/runner/daemon.py`, `labhq/gateway/server.py`, `tests/test_approval_timeout.py`, `tests/test_web.py`.
