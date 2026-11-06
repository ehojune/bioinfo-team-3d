# #1295 — HPC 로그인 상한과 카운터

- 바뀐 것
  - `labhq/hpc_login_budget.py`: SQLite 카운터(`runner.state_dir/hpc_logins.sqlite3`). 로그인마다 한 줄(날짜·요청·명령). 상한이 없어도 센다. `admit`은 요청 한도와 하루 한도를 한 트랜잭션으로 확인하고, 넘으면 `LoginLimitReached`를 던지며 세지 않습니다. `request_scope(request_id)`로 요청을 알려 주면 그 요청의 한도에도 셉니다. 요청을 모르는 로그인은 하루 한도에만 셉니다(요청 한도에 잘못 걸리지 않게).
  - `labhq/settings.py`: `hpc.login_limit_per_request`·`hpc.login_limit_per_day`(기본 **비움**, 기관 값 없음), `hpc.login_limits_decided`. `Settings`가 `runner.state_dir`에서 카운터 파일 위치를 `HpcSettings`에 붙여, `Scheduler(settings.hpc)`만으로 세고 막습니다(`runner/daemon.py`·`hpc_mcp.py`의 기존 `Scheduler(...)` 호출을 바꾸지 않고도).
  - `labhq/tools/scheduler.py`: `_run`이 `ssh_host`일 때 ssh를 열기 **전에** `admit`합니다. 상한에 닿으면 subprocess를 부르지 않습니다. 제출·취소는 그 오류로 실패하고, 일괄 상태 조회는 모든 잡에 그 오류를 돌려줍니다.
  - `labhq/cli.py`: `labhq hpc-logins`(오늘·요청별 사용량과 상한). 예시 설정 두 벌과 manual 04·15장 한·영.
- 연결
  - `labhq/init_wizard.py`: 스케줄러를 SGE·PBS·Slurm으로 고르고 사람이 터미널에 있을 때만 요청당·하루 상한을 묻습니다(`--yes`·파이프는 묻지 않고 정하지 않은 채). 한 번 정했으면 다시 묻지 않습니다.
  - `labhq/runner/daemon.py`: `_on_submit`이 제출을 `request_scope`로 감싸고, 상한에 이미 닿았으면 PI 승인을 묻기 전에 오류로 돌려줍니다. `_poll_jobs`는 열린 잡을 요청별로 묶어 요청마다 일괄 조회 한 번, 요청 범위 안에서 부릅니다. 상한에 닿은 요청의 잡은 조회하지 않고, 그 요청·상한 종류·날짜마다 한 번 `agent.log` 경고(`hpc_login_limit`)를 냅니다. 조회와 확인 사이에 한도가 차면 오류가 아니라 같은 일시 정지로 처리합니다. 하루 상한은 다음 날, 요청 상한은 설정을 올린 뒤 자동으로 다시 확인합니다.
- 시험: `tests/test_hpc_login_budget_1295.py` 33건, fake ssh·가짜 scheduler만(실제 접속 없음, 기관 값 없음). 한도에 닿으면 ssh를 열지 않음, 요청별·날짜별, 재시작 후 이어짐, 동시 호출이 한도를 넘지 않음, 요청 없는 로그인은 하루만, 사용자 답 파싱, 요청별 호출과 범위, 한도에 닿은 요청만 멈춤과 알림 한 번, 하루 상한 전 요청 정지와 올리면 재개, 경쟁 중 한도 도달, init이 묻는 조건. 관련 시험 2243 passed(로컬 Windows).
- 한계: (1) 알림은 `agent.log` 경고이고 PI 승인 카드가 아닙니다. 새 카드 종류는 gateway(`gateway/server.py`)와 web 표시를 함께 바꿔야 해 이번 범위 밖입니다. (2) 직원 도구 `hpc_status`·`hpc_queue`·`hpc_cancel`은 MCP 프로세스에서 직접 ssh를 부르는데 요청을 모르므로 하루 상한에만 셉니다. 세션 관리자 단계에서 함께 정리합니다. (3) 취소와 직원 도구 경로는 상한에 닿으면 오류로 실패합니다.
