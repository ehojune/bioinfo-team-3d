# #1076 rev26: 시간 경쟁 시험 네 개를 안정화한다

- 결론: rev25 집계에서 PR과 무관하게 반복 실패한 시험 중 시험 파일만으로 고칠 수 있는 네 개를 고쳤다. 시험이 잡으려던 결함(loop를 막는 commit·fsync)은 그대로 잡는다. 제품 코드는 바꾸지 않았다.
- 바뀐 것:
  - `test_gateway_loop_lag_606.py`: 느린 commit을 0.2초에서 0.6초로, 한도를 0.3초로. 막힌 loop은 약 0.6초 늦고 러너 지연은 0.2초 이하(CI에서 0.196초까지 봄)라 둘이 만나지 않는다.
  - `test_durable_writer.py::test_loop_stays_responsive_while_fsync_stalls`: 멈춤을 0.5초에서 0.8초로, 간격 한도를 0.4초로(Windows CI에서 0.238초가 나왔다).
  - `test_task_cancel_durable_606.py`: commit이 열린 채 heartbeat가 돌 때까지 기다리게 했다(`threading.Event`). 시간으로 순서를 정하지 않는다.
  - `test_semantics_actions_shadow.py`: `ended` 줄이 쓰일 때까지 기다린 뒤 drain한다. 단언은 그대로 정확히 두 줄이다.
- 실행한 것: 네 파일 75 passed. 취소 시험은 commit을 loop 위에서 돌리는 변형(`aput`을 동기 `put`으로 바꿈)을 만들어 두 변형 모두 실패함을 확인했다(탐지력 유지).
- 미해결/미검증: 한도가 CI의 지연 분포를 실제로 넘는지는 이 PR의 CI와 이후 실행으로 본다. Atlas 자식 프로세스 20초, `test_patch_notes`의 3.10 `SystemError`는 이번 범위 밖이다.
