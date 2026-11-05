# #1190 pause/resume 송신 순서의 전제를 시험으로 고정

- 결론: 낡은 pause/resume 프레임이 새 제어를 덮는 경우는 재현되지 않았다([점검](https://github.com/ehojune/bioinfo-team-3d/issues/1190#issuecomment-6542061968)). `control_seq`는 넣지 않고, 순서가 지켜지는 두 전제를 `tests/test_control_order_1190.py`로 고정했다. 제품 코드는 바꾸지 않았다.
- 전제 1: `send_runner`가 `runner_locks[runner_id]`(FIFO)를 잡고 보낸다 — 첫 송신이 멈춰 있어도 둘째는 앞지르지 못한다.
- 전제 2: `_push_task_state`가 step 상태를 읽은 뒤 `await` 없이 `send_runner`에 들어간다 — 이벤트 루프 턴 수(ticker)가 읽을 때와 들어갈 때 같다.
- 시험(27건): 전제 1·전제 2 각 1건, pause·resume·스냅샷 재전송을 섞어 무작위 양보를 준 수렴 시험 25건(마지막 프레임이 gateway 최종 상태와 일치).
- 변이 확인: 잠금을 빼면 전제 1 시험이, 상태 읽기 뒤에 `await asyncio.sleep(0)`을 넣으면 전제 2 시험이 실패한다(둘 다 직접 확인하고 되돌림).
- 한계: FakeWs 기반이라 실제 WebSocket 지연과 재연결 중 연결이 둘 걸치는 순간은 모사하지 못했다.
