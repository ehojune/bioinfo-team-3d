# #1125 안 C — runner outbox 배치를 한 transaction으로

- 결론: runner의 `_send_batch_now`가 배치 안의 모든 행·메시지를 한 SQLite transaction으로 쓴다. 배치마다 commit·fsync가 한 번이다. 보장은 같거나 강하다(배치는 전부 되거나 전부 안 된다).
- 바꾼 것:
  - `labhq/store.py`: `StateStore.batch()`를 더했다. 안에서 이 thread가 하는 쓰기는 한 transaction이고 commit은 블록 끝에서 한 번이다. 안의 `_transaction`은 savepoint라 자기 오류는 자기 행만 되돌리고, 바깥 오류는 배치 전체를 되돌린다. 중첩 batch는 바깥에 합류한다.
  - `labhq/runner/daemon.py`: `_send_batch_now`가 `store.batch()` 안에서 돈다. 읽는 쪽은 commit된 행만 보므로 outbox 재적재는 batch 안에서 미루고 commit 뒤에 한 번 한다(`_batch_thread`, `_reload_after_batch`).
- 실행한 것(이 PC, 상태 폴더를 D:에 둔 lab 요청 1회, 5단계·이벤트 141건, SQLite가 실제로 실행한 `COMMIT` 문 수):
  - main: runner 315~321 + gateway 533 = **849~854회**. 이 안: runner 246~255 + gateway 533 = **779~788회**(runner lane −21%, 전체 −8%).
  - 요청 처리 시간은 25.3~26.2 s에서 24.0~25.5 s로 측정 잡음(±1 s) 안이다. runner lane은 요청의 임계 경로가 아니라 시간 이득은 이 안 하나로는 측정되지 않는다. 임계 경로는 gateway lane(533회)이라 안 A가 필요하다.
  - 시험: `tests/test_state.py`에 6건(배치는 commit 한 번·순서 유지, 오류 시 전부 취소, 안쪽 쓰기 오류는 savepoint로 자기 행만 취소, 중첩 batch 합류, runner 배치의 commit 한 번과 outbox 재적재 한 번, 실패한 배치는 아무것도 안 남기고 재적재도 안 함)을 더했고 main의 store·daemon에서는 6건 모두 실패한다. 관련 시험 2,177 passed, 30 skipped, #606 시험(`test_durable_writer`·`test_approval_durable_606`·`test_gateway_publish_durable`·`test_runner_shutdown_signals`·`test_loop_liveness_606`) 116 passed.
- 중간에 잡은 결함: 처음 구현은 배치 안의 첫 SAVEPOINT가 명시적 BEGIN 없이 transaction을 열어 그 RELEASE가 곧 COMMIT이었고(실제 commit이 줄지 않음), 시험 `test_store_batch_is_one_commit…`이 잡았다. `batch()`가 `BEGIN IMMEDIATE`를 명시하고, 종료는 연결의 컨텍스트 매니저(`with conn:`)를 그대로 써서 #606 시험이 commit 지연을 거는 지점을 지난다.
- 미확인: CI Windows 전후 시간, 배치가 큰 경우의 효과.
- 근거: #1125 설계 댓글 6531718393, 결정 댓글 6531725554.
