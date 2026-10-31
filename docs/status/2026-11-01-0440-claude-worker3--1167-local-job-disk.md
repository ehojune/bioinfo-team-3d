## 단계
#1167 rev8: 로컬 job 제출 경로에도 셸 명령과 같은 디스크 확인을 붙인다. [PR #1184](https://github.com/ehojune/bioinfo-team-3d/pull/1184)(PowerShell 도구)와 다른 브랜치다.

## 바뀐 것
- `labhq/runner/daemon.py`: `_submit_local_job`이 정책 판정이 `allow`일 때 `_local_job_disk_decision`을 거친다. `disk_space.shell_check`가 `ask`면 판정을 `ask`로 바꿔 기존 PI 카드 경로(`tool_permission`)로 보낸다. 여유는 있으나 낮고 크기를 모르면 `agent.log` 경고만 남긴다. `shell_check`가 예외를 내면 판정을 그대로 둔다. `deny`와 이미 `ask`인 판정은 건드리지 않는다.
- 같은 규칙: 승인 게이트(`approval_mcp._disk_decision`)가 Bash 명령에 하던 것을 runner가 시작하는 job에도 적용한다(runner가 게이트를 거치지 않고 직접 시작하기 때문에 따로 필요했다).

## 시험
- `tests/test_disk_watch_678.py`에 7건(기존 파일): 가득 찬 드라이브로의 unzip이 시작 전에 묻고 거절하면 시작하지 않음, 승인하면 시작, 여유가 넉넉하면 카드 없음, 다운로드·압축 해제가 아닌 명령은 묻지 않음, 크기를 모르고 여유가 낮으면 경고만 하고 시작, 정책이 이미 묻는 명령은 카드 한 장, 확인 오류가 와도 시작. 먼저 써서 수정 전 3건 실패를 확인했다.
- 관련 `test_local_jobs_675.py`·`test_disk_shell_check_678.py`·`test_runner_env_integration_674.py` 포함 159 passed, 2 skipped.

## 하지 않은 것
- `disk_space.py`는 바꾸지 않았다(기존 `shell_check` 재사용).
- manual의 디스크 확인 설명에 "로컬 job 제출도 같은 확인을 받는다"는 문장은 범위 밖이라 manual 차례에 더한다.
- 인벤토리는 새 시험 파일이 없어 건드리지 않았다.
- 실제 Windows·Claude 로컬 job으로 끝까지 돌려 보지는 않았다. 시작 함수(`local_jobs.start`)는 시험에서 대체했다.
