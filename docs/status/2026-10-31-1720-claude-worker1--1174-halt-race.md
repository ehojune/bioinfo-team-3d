# #1174 H1 멈춤 요청과 정상 종료가 겹칠 때

- 결론: `adapters/base.py::halt_watch`가 결과를 `halted`로 표시한 뒤 `agent.log` emit(durable commit이라 양보함)을 기다렸다. 그 사이 CLI가 스스로 끝나 결과가 도착하면 완료된 실행이 `ok=False`·`error="halted by the PI"`로 보고됐다. 표시를 emit 뒤로 옮기고, 표시 직전 `proc.returncode`·`result_arrived`를 다시 본다(그 확인과 `_kill` 사이에 `await`가 없다). 디스크 감시(#1169)가 아무 때나 halt를 걸어 노출이 커졌다.
- 재현: 가짜 claude CLI가 init 뒤 0.4 s에 결과를 내고 끝나고, 시험의 emit이 멈춤 안내에서 1.5 s 양보하면 수정 전 `result.ok=False`(text는 `done`, error `halted by the PI`)였다.
- 바꾼 것: `labhq/adapters/base.py`, `tests/test_halt_watch_1174.py`(2건: 위 경우는 결과 유지, CLI가 아직 돌면 여전히 멈추고 세션 id 보존), `config/ci/windows-boundary-v1.json`.
- 시험: 신규 2건과 어댑터·halt·staff control 관련 79건 통과.
- 미검증: 실제 엔진에서의 발생 빈도, Windows CI. 낮음 2건(L1 `_wake_finished_jobs`, L2 tombstone 정리)은 범위 밖.
