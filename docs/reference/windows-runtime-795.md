# Windows 실행·운영 준비 (#795)

조회용 근거입니다. lifecycle exit 기록 부재는 소멸을 뜻하며 과거 TerminateJobObject 원인을 확정하지 않습니다. PI state·daemon·Job을 조사하거나 조작하지 않았습니다.

- `windows_launch`: CREATE_BREAKAWAY_FROM_JOB 요청 뒤 소유한 Popen handle로 **모든 Job 비소속**을 확인합니다. 거부 시 재시도 없이 실패하며, 잔류·unknown 자식은 소유 handle로 정리합니다. child entry도 소유 기록·자식 실행 전에 검사합니다. foreground에는 강제 gate를 물려주지 않습니다. POSIX session·PID 생성 시각 guard는 유지합니다.
- read-only `current_job`/doctor Job 행의 limits는 **호출자의 immediate Job**만 설명합니다. 상위 Job의 제약과 과거 daemon 소멸 원인은 여기서 알 수 없습니다. launch JSONL은 parent Job 사실·child 비소속·시도/거부/검증/기록을 구분하며 argv·설정·자격 증명을 넣지 않습니다.
- 설정 준비와 `runtime_readiness`는 별도입니다. PID 조회는 cleanup=False입니다. 관리 daemon의 stale·missing·unknown은 기본 doctor도 실패합니다. pristine·정상 down은 비정상 소멸과 구별하며 배포 준비는 아닙니다. `--runtime`은 인증된 gateway 정체와 runner 연결(설정에서 켜진 WSL 포함)도 요구하므로 remote/foreground에 가짜 local PID를 요구하지 않습니다. 기존 설치용 실행 준비 줄은 호환용이며 배포는 `--runtime`을 사용합니다.
- 로컬 검증: mock/static 100 passed. 실제 process/thread/signal·Job fixture 미실행, guard 3개 유지. Python 3.10 AST와 CI fixture 문자열 문법을 검사합니다.
- Windows 실제 CI 전용 `test_windows_job_ci.py`: 임시 fake daemon·state와 unnamed kill-on-close Job을 소유합니다. breakaway 허용/거부, 부모 Job close 뒤 생존 또는 명시적 실패, PID/readiness 부재와 보유 handle 생성 시각·종료 회수를 확인합니다. 상위 CI Job이 허용 경로를 막으면 명시적 실패를 출력하므로 **허용 경로 생존은 그 경우 미검증**입니다. 실제 결과는 CI를 확인해야 하며 로컬 PASS로 대신하지 않습니다. workflow는 추가하지 않습니다.
- manual 165,668→166,205자(LF Unicode).

[Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags): detached console/group은 Job 탈출 증거가 아닙니다. [Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs): 상위 Job에 잔류할 수 있습니다. [QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject): NULL 조회는 immediate Job만 반환합니다.
