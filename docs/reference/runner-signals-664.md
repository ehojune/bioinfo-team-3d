# Runner TERM/HUP 종료 경로 (#664)

- 표준 CLI는 `SIG_DFL`·`SIG_IGN`일 때만 기본 종료 callback을 설치한다. 사용자 callable은 stopping 표시 뒤 위임하고 종료 때 원래 handler를 복원한다. gateway의 기본 watcher 동작은 유지한다.
- runner는 작업 취소·shutdown evidence 기록 뒤 연결이 살아 있으면 task.result ACK를 최대 5초 기다린다. 확인되지 않은 결과는 기존 durable outbox에 남긴다. Ctrl+C와 게시·승인 gate는 바꾸지 않는다.
- 수정 전 기본·무시 TERM/HUP 4건 실패. 수정 후 fake signal·coroutine·gateway 검증 19 passed, 1 deselected: 종료 증거→interrupted→재개 카드, ACK·미확인 outbox, 사용자 handler·Ctrl+C·중복 신호·정리 중 자연 종료를 확인했다.
- 기존 `tests/test_shutdown_recovery.py::test_startup_uses_common_interruption_and_keeps_card`는 process guard가 `subprocess.Popen.__init__`을 차단했다. 실패 전체 로그를 보관했고 재실행하지 않았다. 실제 신호·POSIX/WSL startup·process/proc_tree·운영·HPC는 실행하지 않았다.
- manual 165,483→165,710자(LF Unicode). 이 문서는 조회용 근거다.
