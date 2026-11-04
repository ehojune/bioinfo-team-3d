# #898: 종료 신호 시험의 시간 가정을 고친다

- 결론: `test_standard_entry_cancels_with_evidence_and_mock_gateway_opens_resume[SIGTERM|SIGHUP]`이 main Windows에서 세 번 실패했다(10-30 16:31 SIGHUP `shutdown returned before durable ACK`, 10-30 18:30·10-31 15:57 SIGTERM `gateway_resume timed out`). 시험의 fixture 단계 5초와 `Runner.shutdown`의 기본 5초가 gateway 쪽 fsync가 느린 러너에서 모자랐다.
- 바뀐 것: `tests/test_runner_shutdown_signals.py`만. fixture 단계 한도와 시험이 부르는 `shutdown(timeout_s=...)`을 30초(`FIXTURE_S`)로. 순서 단언(결과가 내구적, ACK 전에는 종료 안 함, 돌려보낼 때 pending 0)은 그대로다. 제품 기본값 5초와 `test_shutdown_keeps_unacknowledged_results_and_has_a_bounded_wait`(짧은 timeout_s로 유지·한도 확인)는 건드리지 않았다.
- 실행한 것: `test_runner_shutdown_signals`·`test_shutdown_recovery` 로컬 통과. Windows 느린 러너 재현은 하지 못했다.
- 미해결/미검증: 30초가 CI 지연을 넘는지는 이 PR 이후 CI로 본다. 가설(시험의 시간 가정과 제품 5초 한도의 어긋남)은 로그 메시지와 코드를 읽어 세웠다.
