# 해제 ACK·durable 재전송 (#1008)

| 항목 | 내용 |
|---|---|
| 명령 | `Hub.release_breaker`가 `command_id`·`episode`를 만들어 `store`(`breaker_release`)에 저장한 뒤 보낸다. `sent`는 전송 사실일 뿐 |
| 재전송 | `_push_task_state`(연결·배정·gateway 재시작)가 ACK 전까지 같은 명령을 다시 보낸다. task 종료 때 만료 |
| ACK | runner가 `agent.breaker`에 `command_id`와 `released`/`stale`을 담아 답한다. gateway `_note_breaker_event`가 같은 ID일 때만 명령을 지운다 |
| runner | `Escalation.episode`(constrain 진입마다 +1), `last_command`. 재전송은 재적용하지 않고 다시 ACK, 다른 episode는 `stale` |
| 시험 | `tests/test_breaker_enforcement.py`(+2, 1 수정) |
| 미확인 | 실제 network E2E, runner 재시작 뒤 복구(runner 메모리의 episode·last_command는 재시작 시 초기화), CSO 승인, compaction |
