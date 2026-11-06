# #1299 — HPC 세션 관리자 (#1295 B단계)

- 바뀐 것
  - `labhq/hpc_session.py`: 독립 모듈. 사이트 값은 `SessionConfig`로 받습니다(접속 `argv`, `idle_lock_s`, `heartbeat_s`, 시간·출력 상한, 재접속 횟수·대기·냉각, host key·터널 문구). 저장소에 기관 값은 없습니다.
  - 명령 한 줄을 `printf` begin 표지와 end 표지(`$?` 포함) 사이에 보냅니다. 출력은 두 표지 사이이고 종료 코드는 end 표지에서 읽습니다. 배너·프롬프트·터미널 echo는 버립니다. 첫 명령이 `stty -echo`라 echo가 꺼집니다. 원격 명령을 무시하는 gateway에서도 stdin으로만 동작합니다.
  - 명령은 한 번에 하나(`RLock`), 명령마다 시간 제한과 출력 상한(넘치면 읽되 버리고 `truncated`). 시간 초과는 세션을 닫습니다.
  - `tick()`(또는 `start()`의 백그라운드 스레드): 유휴가 `heartbeat_s`를 넘으면 `true`를 보내고, 열린 잡이 없는데 `idle_close_s`를 넘으면 닫습니다. `heartbeat_s`는 `idle_lock_s`보다 짧아야 하고, 기본은 잠김의 1/4(최대 900초)입니다. 잠김 3시간이면 900초입니다.
  - 접속마다 #1298 `LoginBudget.admit`을 거칩니다. 재접속은 `reconnect_attempts`번, 대기는 두 배씩 늘고, 다 쓰면 냉각 동안 로그인 없이 `ReconnectFailed`입니다. 상한에 닿으면 열지 않고 `on_notice("login_limit")` 후 `LoginLimitReached`를 올립니다.
  - host key가 바뀌면 `HostKeyChanged`로 멈추고 `reset()` 전까지 재시도하지 않습니다. 터널이 없으면 `NeedsHuman`으로 사람에게 알리고 `resume()`까지 접속을 시도하지 않습니다.
- 시험: `tests/test_hpc_session_1299.py` 16건, `tests/fake_hpc_gateway.py`(가짜 gateway)만 씁니다. 원격 실행 무시, 배너·echo, 큰 출력, 시간 초과, 끊김, 유휴 잠김과 heartbeat, 유휴 닫기, 카운터, 재접속 상한과 냉각, host key, 터널, 동시 호출.
- 미연결: 도구·runner 연결은 C단계입니다. 이 PR은 모듈과 시험만 담습니다.
- 검증하지 않은 것: 실제 ssh·실제 HPC·실제 PTY(파이프 위에서 시험), Windows OpenSSH의 `-tt` 동작, 사이트별 문구. 원격 셸은 POSIX `sh`로 가정합니다(`printf`, `$?`, `stty`).
