# #1125 안 D — runner ack 합치기

- 결론: gateway는 runner의 outbox 행마다 ack를 보내고 runner는 ack마다 commit·fsync를 했다. ack는 누적(`DELETE … WHERE seq<=?`)이라, 진행 중인 commit 동안 도착한 ack를 다음 commit 하나로 합친다. 지연 fsync나 타이머는 없다.
- 바꾼 것(`labhq/runner/daemon.py`):
  - `_ack_nowait(seq)`: 가장 높은 seq를 적고, 진행 중인 flush가 없으면 즉시 시작한다. `_ack_flush`는 commit하는 동안 높아진 seq까지 한 번 더 commit하고 끝난다.
  - 연결 루프는 `runner.ack`를 기다리지 않고 읽는다. 다음 메시지를 읽는 일이 ack의 fsync에 막히지 않는다.
  - `_on_message({"type":"runner.ack"})`를 직접 부르는 호출자는 전과 같이 durable이 된 뒤 돌아온다(`_ack_drain`).
  - 연결이 끝날 때(재연결 전), `shutdown()`, `run_forever()` 종료 때 `_ack_drain`으로 이미 받은 ack를 commit한다. commit이 실패하면 다음 ack 또는 기다리는 호출자에게 같은 예외가 간다(전에는 인라인 commit이 연결 루프로 올렸다).
- 안전: ack가 늦거나 없어져도 행이 남아 재전송될 뿐이고, gateway가 `runner_cursor`(`runner_seen`)로 중복을 걸러낸다(`test_runner_job_recovery_outbox_and_gateway_dedup`).
- 시험(`tests/test_state.py` 5건, `tests/test_runner_shutdown_signals.py`의 `SimpleNamespace` 대역에 `_ack_drain` 추가): ack가 commit 중 도착하면 commit 둘(첫 ack 단독 + 나머지 누적)로 끝남, 아무도 기다리지 않으면 지연 없이 commit, `_on_message` ack는 durable 뒤 반환, commit 실패는 다음 ack·기다리는 호출자에게 전달되고 행은 남음, shutdown은 이미 받은 ack를 commit. 이전 daemon에서는 이 중 동작 시험 4건이 실패한다. 관련 시험 2,799 passed, 35 skipped.
- 실측(이 PC, 상태 폴더 D:, lab 요청 1회, main에는 안 A가 반영됨): 실제 `COMMIT` 문 main 613~624회 → 548회(runner 221~227 → 151, −31%). 요청 처리는 main 20.9~35.5 s(중앙값 약 22 s)에서 19.9~21.6 s(중앙값 약 20.6 s)로, D: 디스크 편차가 커서 3~7%대 감소와 편차 축소 정도로만 본다.
- 미확인: CI Windows 전후 시간, ack가 더 크게 몰리는 느린 디스크에서의 효과.
- 근거: #1125 설계 댓글 6531718393.
