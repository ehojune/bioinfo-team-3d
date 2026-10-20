# Windows 실행·운영 준비 (#795)

조회용 근거입니다. lifecycle exit 기록 부재만으로 과거 TerminateJobObject 원인을 확정하지 않습니다. PI state·daemon·Job은 조사하거나 조작하지 않았습니다.

## Launch 계약

- direct: BREAKAWAY와 모든 Job 비소속을 확인합니다. Windows venv redirector는 별도 프로세스·Job을 만들므로 실제 base interpreter와 CPython의 `__PYVENV_LAUNCHER__`로 venv를 유지합니다. foreground/POSIX는 바꾸지 않습니다.
- fallback: WinError 5 또는 확인된 Job 잔류에만 supervisor용 transient local WMI를 한 번 시도합니다. 잔류 direct 자식은 Popen의 보유 handle로 종료·회수한 뒤 진행합니다. unknown·정리 실패에는 WMI를 부르지 않습니다.
- WMI: system PowerShell의 고정 encoded script가 stdin JSON을 받아 Win32_Process.Create를 호출합니다. absolute interpreter/cwd, 구조화 argv, BREAKAWAY|DETACHED|UNICODE, hidden window를 사용합니다. 서비스·Task Scheduler·설치·승격·host 정책 변경은 없습니다.
- Create 자체에는 env 인자가 없지만 공식 Startup.EnvironmentVariables 필드가 있습니다. 환경 값은 stdin/COM 메모리로만 전달하고 디스크·argv·진단에는 넣지 않습니다. provider가 환경을 무시하면 bootstrap의 digest 대조가 실패합니다.
- bootstrap: instance state의 임시 폴더에 current-user-only 상속 DACL을 적용합니다. control에는 nonce·20초 만료·PID 정체·target 경로·환경 digest를 두며 자격 증명은 없습니다. site/PYTHONPATH 로딩 없이 시작하고 target 진입 때 기존 경로·prefix를 복구합니다. 이 경로의 초기 Python isolated/no-site flags는 유지됩니다.
- ACK 전: caller 생존·생성 시각, nonce receipt의 PID/생성 시각, 보유 handle의 executable/current-user, **ANY-Job 비소속**을 확인합니다. unknown·거부·timeout·불일치는 실패합니다. 신원 미확정 PID는 죽이지 않으며 ACK 없는 bootstrap은 만료/부모 소멸 시 self-abort합니다.
- ACK는 마지막 commit 단계입니다. 이후 handshake timeout으로 이미 진입한 supervisor만 죽이는 rollback은 없습니다. bootstrap은 caller/Job을 다시 확인하고 같은 PID로 target을 실행합니다. 실제 role readiness와 PID 생성 시각 guard는 기존 CLI가 확인합니다. 제어 파일은 소비/중단 때 지우며 cleanup 실패가 난 파일에도 환경 값은 없습니다.

## 준비 판정·증거

설정 준비와 runtime 준비는 별도입니다. stale/missing/unknown managed daemon은 실패하고 pristine·정상 down은 구별합니다. `started_here=False`의 실제 health 함수·autospec 회귀를 유지합니다. remote/foreground와 켜진 WSL은 인증된 gateway 연결로 확인합니다.

[head 0bf4c97d clean-install](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39363311454/job/118434304160)은 child_in_job=True/query={}로 실패했습니다. 조회는 성공했고 SOME Job에 남았습니다. parent immediate flags 12288은 KILL_ON_CLOSE|SILENT_BREAKAWAY_OK이며 **상위 Job flags는 모릅니다**. 새 broker가 이 환경에서 성공한다는 근거는 아직 없습니다.

broker 단계 로컬은 guard 3개를 유지한 mock/static 146 passed, blocked 0과 Python 3.10 AST만 검증했습니다. 실제 WMI/process/thread/signal은 실행하지 않았습니다. CI 전용 positive는 fake supervisor/role의 launch 성공·부모 Job close 뒤 생존·소유 handle 정리를 요구합니다. nested outer non-breakaway/inner silent-breakaway WMI positive와 별도 unknown/timeout·direct 실제 거부+provider mock 거부가 있습니다. 거부를 생존 PASS로 세지 않습니다. WMI provider가 탈출을 막으면 CI는 실패하며 안전 gate를 낮추지 않습니다. workflow는 바꾸지 않습니다.

root 배포 gate: `doctor --runtime` rc0/runtime_readiness.ready, 실제 WSL, 명시적 idle0, global deploy lock. 이 작업은 배포하지 않습니다.

[WMI Create](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/create-method-in-class-win32-process) · [Startup](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-processstartup) · [Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs) · [CPython venv redirector](https://github.com/python/cpython/blob/3.12/PC/launcher.c)
## encoding·공용 fixture 후속

binary tail은 `open(mode="rb")`, Native handle은 `open_process`로 명시해 기존 encoding classifier를 그대로 통과합니다. 64 KiB seek/tail과 ACK·ownership·cleanup은 유지합니다. `tests/test_text_encoding.py`를 고치지 않아 PR #793의 HTTP binary 범위와 충돌하지 않습니다. 실제 classifier의 잘못된 text I/O 반례와 bounded-tail fake도 검사합니다.

공용 test만 원문 재사용: `6954ab6aa3ce7314744d754dea16d26e45c0731d`의 recruit/receipt-clock 2파일, `98f70d96a937e5c6ee8fb650a31d5bf63c0eecbe`의 **second parent 대비 external_wait 1파일 delta**. 결과 blob을 원본과 대조했습니다. timeout을 늘리지 않고 controlled clock·취소·progress timeout 단정을 보존합니다. 새 focused mock/static 110 passed, guard blocked 0. 실제 Hub/process/WMI fixture와 fresh-head CI는 root 확인 대상입니다.
