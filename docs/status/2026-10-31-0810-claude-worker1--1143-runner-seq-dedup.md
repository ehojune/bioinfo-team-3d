# #1143 같은 runner 메시지가 동시에 두 번 처리되는 문제

- 결론: `Hub.on_runner_message`가 처리 중인 `(runner_id, incarnation, runner_seq)`를 메모리 집합에 두어(첫 await 전의 동기 구간) 같은 행의 두 번째 사본은 첫 처리기가 끝날 때까지 **기다린 뒤**(바로 돌아가면 새 연결의 다음 seq가 커서를 먼저 올려 첫 사본의 이벤트가 "이미 봄"으로 버려진다) 보통 경로로 가서 seq가 저장돼 있으면 ack만 하고 돌아간다. 첫 처리기가 실패·취소되면 사본이 직접 처리한다. 두 사본 모두 ack를 받는다. 저장소는 마지막 방어선이다: `_append_event_now`가 트랜잭션 안에서 커서를 읽어 이미 본 seq면 이벤트를 넣지 않고 `None`을 돌려주며(`publish`가 그대로 끝냄), 커서 갱신(`_append_event_now`·`_mark_runner_now`)은 `WHERE excluded.seq > runner_cursor.seq`로 앞으로만 움직인다.
- 원인(#1139 rev2): `runner_seen` 확인이 트랜잭션 밖이었고 커서 갱신이 무조건 덮어써서, 옛 연결과 새 연결이 같은 outbox 행을 동시에 처리하면 이벤트가 두 번 저장됐다.
- 시험(`tests/test_state.py`): 같은 seq 동시 두 번 → 이벤트 1건(수정 전 2건)이고 두 사본 모두 ack, 재전송이 첫 사본을 기다려 다음 seq(6)가 5를 앞지르지 못함(즉시 반환 구현에서는 실패), 첫 사본이 취소되면 재전송이 처리됨, 커서가 뒤로 안 가고 이미 본 seq는 이벤트를 안 넣음(수정 전 새 이벤트가 들어감), 같은 `task.result`를 두 seq로 보내도 비용은 한 번만 기록됨(이미 task id로 멱등이라 수정 전에도 통과).
- 비용 이중 기록 확인: `_arecord_task_cost`가 `cost_by_task`에 task id가 있으면 건너뛰므로 같은 `task.result`가 다른 seq로 두 번 와도 `cost_usd`는 한 번만 더해진다(시험으로 고정).
- 바꾼 것: `labhq/gateway/server.py`(`on_runner_message` 바깥 wrapper, `publish`·`_append_event_with_request`의 `None` 처리), `labhq/store.py`, `tests/test_state.py`.
- 알려진 것: `tests/test_state_activity_durable.py::test_concurrent_activity_merges_tools_and_owns_inputs`는 서로 다른 두 메시지에 같은 `runner_seq: 1`을 줘서 새 중복 방지에 걸린다(실제 runner는 seq를 되풀이하지 않음). 그 시험 도우미가 범위 밖이라 아직 고치지 않았고 push도 하지 않았다.
- 미검증: 실제 재연결 중복 도착, 메시지 종류별 멱등성(비용 외).
