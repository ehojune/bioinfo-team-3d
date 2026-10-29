# 폭주 차단기 후속 (#1008)

| 항목 | 내용 |
|---|---|
| P2 1 | runner의 breaker halt를 `agent.control`로 알리고, gateway가 `agent.breaker(enforcement=halt)`를 받으면 `halt_requested`·step `halt_op`을 기록(`Hub._note_runner_halt`) |
| P2 3 | constrain·stop 단계에서는 `Breaker.repeating`만 신호로 쓴다. 거부된 호출의 오류 연속은 승격하지 않음(manual 명시) |
| P2 2 | 끊긴 runner에 보낸 해제는 `_breaker_release_pending`에 두고 `_push_task_state`가 재연결 때 한 번 재전송. gateway 재시작 시 사라짐 |
| P2 4 | `_run_guarded` 주석 분리 |
| 작업판 | `control.constrained`면 "제한 해제" 버튼(`POST /api/tasks/{id}/breaker/release`) |
| 시험 | `test_breaker_enforcement.py`(+2), `web_staff_control.cjs`. 관련 staff_control py 72 passed(timing flake 1건은 단독 재실행 통과) |
| 범위 밖 | constrain 때 CSO 승인, compaction 예외(범위·크기는 PR 본문), 실제 network E2E |
