# #1295 — HPC 로그인 상한과 카운터 (진행 중, init·runner 연결 대기)

- 바뀐 것
  - `labhq/hpc_login_budget.py`: SQLite 카운터(`runner.state_dir/hpc_logins.sqlite3`). 로그인마다 한 줄(날짜·요청·명령). 상한이 없어도 센다. `admit`은 요청 한도와 하루 한도를 한 트랜잭션으로 확인하고, 넘으면 `LoginLimitReached`를 던지며 세지 않습니다. `request_scope(request_id)`로 요청을 알려 주면 그 요청의 한도에도 셉니다. 요청을 모르는 로그인은 하루 한도에만 셉니다(요청 한도에 잘못 걸리지 않게).
  - `labhq/settings.py`: `hpc.login_limit_per_request`·`hpc.login_limit_per_day`(기본 **비움**, 기관 값 없음), `hpc.login_limits_decided`. `Settings`가 `runner.state_dir`에서 카운터 파일 위치를 `HpcSettings`에 붙여, `Scheduler(settings.hpc)`만으로 세고 막습니다(`runner/daemon.py`·`hpc_mcp.py`의 기존 `Scheduler(...)` 호출을 바꾸지 않고도).
  - `labhq/tools/scheduler.py`: `_run`이 `ssh_host`일 때 ssh를 열기 **전에** `admit`합니다. 상한에 닿으면 subprocess를 부르지 않습니다. 제출·취소는 그 오류로 실패하고, 일괄 상태 조회는 모든 잡에 그 오류를 돌려줍니다.
  - `labhq/cli.py`: `labhq hpc-logins`(오늘·요청별 사용량과 상한). 예시 설정 두 벌과 manual 15장 한·영.
- 시험: `tests/test_hpc_login_budget_1295.py` 27건, fake ssh만(실제 접속 없음, 기관 값 없음). 한도에 닿으면 ssh를 열지 않음, 요청별·날짜별, 재시작 후 이어짐, 동시 호출이 한도를 넘지 않음, 요청 없는 로그인은 하루만, 사용자 답 파싱.
- 아직 안 한 것(claim 밖 파일): `labhq init`이 상한을 묻는 일(`labhq/init_wizard.py`), runner가 자기 제출·조회·취소를 `request_scope`로 감싸는 일과 상한에 닿았을 때 상태 조회를 멈추고 PI 카드로 묻는 일(`labhq/runner/daemon.py`). 이 둘이 들어와야 요청당 상한이 실제로 작동합니다. 지금은 요청을 모르는 채 하루 상한만 집행됩니다.
- 한계: 직원 도구(`hpc_status`·`hpc_queue`·`hpc_cancel`)가 MCP 프로세스에서 직접 ssh를 부르는 경로는 요청을 모르므로 하루 상한에만 셉니다.
