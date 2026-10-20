# Runner TERM/HUP 종료 경로 (#664)

- 표준 CLI는 `SIG_DFL`·`SIG_IGN`일 때만 기본 종료 callback을 설치한다. 사용자 callable은 stopping 표시 뒤 위임하고 종료 때 원래 handler를 복원한다. gateway의 기본 watcher 동작은 유지한다.
- runner는 작업 취소·shutdown evidence 기록 뒤 연결이 살아 있으면 task.result ACK를 최대 5초 기다린다. 확인되지 않은 결과는 기존 durable outbox에 남긴다. Ctrl+C와 게시·승인 gate는 바꾸지 않는다.
- 수정 전 기본·무시 TERM/HUP 4건 실패. 수정 후 fake signal·coroutine·gateway 검증 19 passed, 1 deselected: 종료 증거→interrupted→재개 카드, ACK·미확인 outbox, 사용자 handler·Ctrl+C·중복 신호·정리 중 자연 종료를 확인했다.
- 기존 `tests/test_shutdown_recovery.py::test_startup_uses_common_interruption_and_keeps_card`는 process guard가 `subprocess.Popen.__init__`을 차단했다. 실패 전체 로그를 보관했고 재실행하지 않았다. 실제 신호·POSIX/WSL startup·process/proc_tree·운영·HPC는 실행하지 않았다.
- manual 165,483→165,710자(LF Unicode). 이 문서는 조회용 근거다.
- PR #790 P1 후속: diagnostics가 설치한 wrapper의 원래 disposition만 해석하여 기본 TERM은 graceful 경로로 보내고 사용자 handler는 진단 기록과 함께 위임한다. 원래 wrapper·OS handler는 순서대로 복원한다. durable acceptance 뒤 stopping을 다시 검사하여 늦은 dispatch를 실행하지 않고 종료 증거가 있는 실패 결과를 기록하며 중복 요청은 기존 결과를 보존한다.
- 후속 검증: 수정 전 3건 실패·2건 통과, 수정 후 mock/static 24 passed·1 deselected. 기존 guard 차단 startup 테스트는 재실행하지 않았다. 기록 P1 두 건은 `83d5bf89`의 행·status와 그 head의 patch-notes 검사 통과로 stale이다. manual 165,710→165,749자.
- P1 4320895582: dispatch를 첫 await 전에 등록하고 acceptance·중단 결과 기록이 끝날 때까지 종료 drain이 기다린다. task 취소 중 추가된 handler도 재검사하며 timeout은 completion future를 취소하지 않는다. 메모리 store·coroutine barrier로 단일/복수 acceptance, 결과 commit, 연결 유무·ACK, timeout·예외, task 정리 도중 dispatch를 검사했다. 수정 전 5 failed/1 passed와 추가 interleaving 1 failed, 수정 후 관련 mock/static 19 passed. 실제 writer thread·신호·process·운영 검증은 없으며 기존 차단 startup은 재실행하지 않았다.
