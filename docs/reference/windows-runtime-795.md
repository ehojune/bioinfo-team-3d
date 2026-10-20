# Windows 실행·운영 준비 (#795)

조회용 근거입니다. lifecycle exit 기록 부재는 소멸을 뜻하며 과거 TerminateJobObject 원인을 확정하지 않습니다. PI state·daemon·Job을 조사하거나 조작하지 않았습니다.

- `windows_launch`: CREATE_BREAKAWAY_FROM_JOB 요청 뒤 소유한 Popen handle로 **모든 Job 비소속**을 확인합니다. 거부 시 재시도 없이 실패하며, 잔류·unknown 자식은 소유 handle로 정리합니다. child entry도 소유 기록·자식 실행 전에 검사합니다. foreground에는 강제 gate를 물려주지 않습니다. POSIX session·PID 생성 시각 guard는 유지합니다.
- read-only `current_job`/doctor Job 행의 limits는 **호출자의 immediate Job**만 설명합니다. 상위 Job의 제약과 과거 daemon 소멸 원인은 여기서 알 수 없습니다. launch JSONL과 오류 출력은 parent Job·child 잔류(True)/조회 불가(null)·Win32 오류·binding 오류를 구분하며 argv·설정·자격 증명을 넣지 않습니다.
- 설정 준비와 `runtime_readiness`는 별도입니다. PID 조회는 cleanup=False입니다. 관리 daemon의 stale·missing·unknown은 기본 doctor도 실패합니다. pristine·정상 down은 비정상 소멸과 구별하며 배포 준비는 아닙니다. `--runtime`은 인증된 gateway 정체와 runner 연결(설정에서 켜진 WSL 포함)도 요구하므로 remote/foreground에 가짜 local PID를 요구하지 않습니다. 기존 설치용 실행 준비 줄은 호환용이며 배포는 `--runtime`을 사용합니다.
- health 회귀: 실제 `check_health`와 autospec 5건이 수정 전 필수 keyword 누락 TypeError를 재현했습니다. 읽기 전용 검증은 `started_here=False`를 넘기며 다른 identity·identity 없는 gateway는 계속 거부합니다.
- ctypes mock: HANDLE 인수·반환은 pointer 크기, BOOL 반환·출력은 32bit이며 subprocess.Handle과 같은 int subclass의 64bit 값이 잘리지 않습니다. 기존 바인딩에서 잘림 결함은 재현되지 않았습니다. GetCurrentProcess의 빈 argtypes도 명시했습니다. 실제 Win32/outer host 상태는 로컬에서 검사하지 않았습니다.
- Windows CI positive는 **성공 launch → 부모 Job close → child 생존 → 소유 handle 정리**를 요구합니다. 거부/unknown은 `UNVERIFIED positive survival` 실패이며 PASS로 치지 않습니다. 별도 denied test는 WinError 5와 PID/readiness 부재를 검증합니다. 로컬에서는 이 fixture를 통째로 fake로 바꾼 계약 검사만 수행하며 실제 process/thread/signal·Job은 실행하지 않습니다. guard 3개와 workflow를 유지합니다.
- 후속 로컬 mock/static 107 passed. 실제 health 함수·autospec, native 오류/64bit handle fake, positive 계약의 거부/unknown 배제와 기존 supervisor 검증입니다. Python 3.10 AST(embedded CI script 포함) PASS. manual 166,205→166,295자(LF Unicode).

## 완료된 CI 실패와 남은 증거

[clean-install job 118429060176](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39361662285/job/118429060176)은 2026-10-20 02:58:15Z에 launch exit 2로 실패했습니다. 해당 메시지는 Popen 생성 뒤 Job 검증 실패 경로입니다. 당시 출력에는 child_in_job과 Win32 오류가 없어 **잔류와 조회 오류 중 어느 쪽인지 미확정**입니다. 위 진단 추가는 이 clean-install 회귀의 해결 증거가 아닙니다.

다음 Windows CI에서 native 오류와 positive 생존을 확인해야 합니다. 상위 host Job 잔류가 확인되면 모든 상위 Job이 breakaway를 허용하는 별도 실행 환경 또는 사전 검토된 독립 broker가 필요합니다. 이 PR은 host 정책·서비스를 만들거나 변경하지 않습니다. root 배포 gate는 `doctor --runtime` exit 0 **및** `runtime_readiness.ready=true`이며 아직 배포 근거가 없습니다.

[Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags): detached console/group은 Job 탈출 증거가 아닙니다. [Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs): 상위 Job에 잔류할 수 있습니다. [QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject): NULL 조회는 immediate Job만 반환합니다.
[IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob): NULL Job은 전체 Job membership, 반환 0은 조회 실패이며 GetLastError로 구분합니다.
