# #1125 안 C — runner outbox 배치를 한 transaction으로

- 결론: runner의 `_send_batch_now`가 배치 안의 모든 행·메시지를 한 SQLite transaction으로 쓴다. 배치마다 commit·fsync가 한 번이다. 보장은 같거나 강하다(배치는 전부 되거나 전부 안 된다).
- 바꾼 것:
  - `labhq/store.py`: `StateStore.batch()`를 더했다. 안에서 이 thread가 하는 쓰기는 한 transaction이고 commit은 블록 끝에서 한 번이다. 안의 `_transaction`은 savepoint라 자기 오류는 자기 행만 되돌리고, 바깥 오류는 배치 전체를 되돌린다. 중첩 batch는 바깥에 합류한다.
  - `labhq/runner/daemon.py`: `_send_batch_now`가 `store.batch()` 안에서 돈다. 읽는 쪽은 commit된 행만 보므로 outbox 재적재는 batch 안에서 미루고 commit 뒤에 한 번 한다(`_batch_thread`, `_reload_after_batch`).
- 실행한 것(이 PC, 상태 폴더를 D:에 둔 lab 요청 1회, 5단계·이벤트 141건):
  - 실제 commit 약 850회 → 약 787회(−63, −7%). commit fsync 합 32.8 s → 25.5 s.
  - 요청 처리 시간은 25~26.5 s 그대로다(후 25.2~26.2 s). runner lane은 요청의 임계 경로가 아니라 시간 이득은 이 안 하나로는 측정되지 않았다. 임계 경로는 gateway lane이라 안 A가 필요하다.
  - 관련 시험 1,516 passed, 18 skipped(`-k "runner or outbox or enqueue or store or state or durable or daemon or recover or resume or accepted"`), #606 시험(`test_durable_writer`·`test_approval_durable_606`·`test_gateway_publish_durable`·`test_runner_shutdown_signals` 등) 425 passed.
- 미확인: CI Windows 전후 시간, 배치가 큰 경우(여러 메시지 한 배치)의 효과. 새 동작을 고정하는 시험은 `tests/test_state.py`에 넣을 예정이다(편집 전 claim 요청).
- 근거: #1125 설계 댓글 6531718393, 결정 댓글 6531725554.
