# #1295 — HPC 로그인이 생기는 경로와 연결을 줄이는 방법 (조사)

읽기 전용 조사입니다. 실제 HPC 접속과 코드 수정은 하지 않았습니다(main `d8f73f2e` 기준 코드 읽기, 이 PC의 OpenSSH 확인, 공개 자료 1건). 구현은 PI 승인 뒤입니다.

## 1. ssh를 여는 경로 (전수)

`labhq/`에서 ssh를 여는 곳은 **한 줄**입니다.

| 위치 | 동작 |
|---|---|
| `labhq/tools/scheduler.py:325` `Scheduler._run` | `hpc.ssh_host`가 있으면 모든 scheduler 명령을 `ssh -o BatchMode=yes <host> <명령>`로 감쌉니다. 명령 하나 = ssh 프로세스 하나 = 로그인 하나 |

`_run`을 부르는 명령과 부르는 곳:

| 호출 | ssh 수 | 누가 언제 |
|---|---|---|
| `submit` (`qsub`/`sbatch`) | 1 | runner가 agent의 `hpc_submit`을 받아 한 번. 재시도 없음(이중 제출 방지) |
| `status` — SGE | 1~2 | `qstat -u`(1), 잡이 큐에 없으면 `qacct -j`(+1) |
| `status` — Slurm | 1~2 | `squeue -j`(1), 이미 끝났으면 `sacct -j`(+1) |
| `status` — PBS | 1 | `qstat -f` |
| `queue` (`hpc_queue`) | 1 | agent가 부를 때 |
| `cancel` | 1 | agent·PI가 취소할 때 |

호출하는 쪽은 둘입니다.

1. **runner의 잡 감시 루프** `runner/daemon.py` `_job_watch_loop`/`_poll_jobs`(3600행 이후). `runner.job_poll_s`(기본 **60 s**)마다 **종료되지 않은 잡 하나하나**에 `scheduler.status`를 부릅니다. 잡을 묶어서 한 번에 묻지 않습니다. 끝났어도 SGE의 accounting 지연 때문에 `missing`이 3번까지 이어질 수 있습니다(`MISSING_POLLS_BEFORE_FINISHED=3`).
2. **agent가 부르는 도구** `tools/hpc_mcp.py`의 `hpc_status`·`hpc_queue`·`hpc_cancel`. 도구 설명이 "루프로 조회하지 말라"고 적고 있지만 코드로 막지는 않습니다.

ssh를 열지 **않는** 곳도 확인했습니다. `doctor`는 원격 호스트를 접속하지 않고("remote host configured, not contacted", `doctor.py:1140`), `hpc_consult`의 조사 명령은 로컬에서만 돌며 `ssh_host`를 쓰지 않습니다.

## 2. 요청 1건의 로그인 수 (추정)

기본값 `job_poll_s=60` 기준입니다. 실제 클러스터에서 센 값이 아니라 코드에서 계산한 값이고, 클러스터가 ssh 연결을 어떻게 기록하는지(연결당인지 세션당인지)는 확인하지 못했습니다.

| 시나리오 | 계산 | 로그인 |
|---|---|---|
| 잡 1개, 기본 walltime 4 h, PBS | 제출 1 + 240 polls × 1 | **약 241** |
| 같은 잡, SGE/Slurm이 끝난 뒤 accounting을 한 번 더 묻는 경우 | + 끝난 뒤 1~4 | 약 245 |
| 잡 3개가 4 h 동안 동시에 실행 | 제출 3 + 3 × 240 | **약 723** |
| 큐 대기 2 h + 실행 4 h, 잡 1개 | 360 polls | 약 361 |
| agent가 `hpc_status`를 분마다 부름(도구 설명을 어김) | 위에 더해 | +60/시간 |
| gateway/runner를 재시작해도 잡이 남아 있음 | 남은 잡마다 계속 | 위와 같음 |

한 요청에서 **수백 건**이 쌓이고, 잡 수와 길이에 비례합니다. 제출과 취소는 1건씩이라 로그인 수의 거의 전부가 상태 조회입니다.

## 3. 로그인을 줄이는 방법

연결을 유지하는 방법(4절)보다 **먼저 줄일 수 있는 것**이 있어서 따로 적습니다. 효과가 크고 새 의존성이 없습니다.

| 방법 | 효과 | 새로 필요한 것 | 비고 |
|---|---|---|---|
| **A. 한 번에 묻기** | 잡 N개 → 로그인 N배 → 1배 | 코드 변경만 | SGE `qstat -u`(이미 사용자 전체를 줌), Slurm `squeue -j id1,id2,…`·`sacct -j id1,id2,…`, PBS `qstat -f id1 id2 …`. 지금은 SGE도 잡마다 `qstat -u`를 다시 부릅니다 |
| **B. 조회 간격 늘리기·backoff** | 60 s → 5~10 min이면 시간당 60 → 6~12 | 설정값 | 짧은 잡은 늦게 알림. 처음엔 짧게, 오래 돌수록 간격을 늘리는 방식이 자연스럽습니다 |
| **C. 상한** | 하루·요청당 로그인 상한 초과 시 조회를 멈추고 PI에 알림 | 카운터 | 이슈의 "하루 로그인 상한"과 같은 것. 상한에 닿으면 취소가 아니라 조회만 멈춰야 합니다 |
| **D. 완료 표지 파일** | 감시의 ssh를 거의 0으로 | `build_script`를 바꿔 본문과 분리된 wrapper가 상태 파일을 씀 | 아래 설명. **지금 stdout 로그의 `[labhq] exit=`는 표지로 쓸 수 없음** |

**D 설명.** 지금의 `[labhq] exit=… end=…` 줄(`build_script`의 `EXIT` trap, `scheduler.py:152`)은 **완료 표지로 신뢰할 수 없습니다.** 그 줄은 잡 본문과 같은 stdout 로그에 섞여 있어서, 본문이 같은 줄을 먼저 출력하거나 trap을 바꿔 버리면 아직 도는 잡을 끝난 것으로 오판합니다(봇 리뷰 지적). 표지로 쓰려면 `build_script`를 바꿔야 합니다: **본문을 자식 프로세스로 돌리고, 그 종료 뒤에 wrapper가 별도 상태 파일(`jobs/<이름>.status`: 종료 코드·scheduler 잡 id·끝 시각)을 임시 파일에 쓴 뒤 원자적으로 rename**하게 합니다. runner는 이 상태 파일만 믿고 로그 본문은 읽지 않습니다. 본문이 상태 파일을 위조할 수 있는 경로(같은 작업 폴더에 쓰기 권한)가 남아 있으므로, 상태 파일 값은 완료의 **힌트**로만 쓰고 완료를 기록하기 전에 **scheduler 상태로 한 번 확인**(ssh 1건)합니다. wrapper는 scheduler 잡이 실제로 끝나기 **전에** 상태 파일을 쓰고, SGE는 끝난 뒤에도 accounting이 늦을 수 있어서(`missing`, 지금도 3번까지 기다림) 상태 파일을 본 직후의 확인 한 번이 `running`·`missing`을 줄 수 있습니다. 그래서 확인은 한 번으로 끝내지 않고 **scheduler가 종료 상태를 줄 때까지 제한된 backoff로 재확인**합니다(예: 30 s, 1 min, 2 min, 5 min, 이후 5 min 간격, 잡당 상한 12회, 상한에 닿으면 PI에 알림). 같은 상태 파일을 매 poll마다 다시 확인하면 로그인이 도로 쌓이므로 확인 횟수를 잡마다 세어 둡니다. 그 결과 상태 조회는 "잡마다 60 s"(4 h 잡이면 약 240건)에서 "잡 하나당 완료 때 약 2~6건"으로 줄어듭니다(backoff 예시 기준 계산, 실측 아님). `ssh_host`를 쓰는 구성은 작업공간이 **runner가 볼 수 있는 공유 파일시스템**에 있어야 하므로(`docs/manual/15-config.md:31`) 로컬에서 그 파일을 읽을 수 있습니다. ssh는 제출, 상태 파일이 보인 뒤의 확인 1건, 상태 파일 없이 walltime이 지났을 때의 확인(강제 종료·OOM은 wrapper가 못 돎), 취소에만 씁니다. 한계는 공유 FS의 파일 지연(NFS 캐시)이고, **이 PC에서 공유 FS를 실제로 볼 수 있는 사이트에서만 성립**하므로 사이트 프로필 선택으로 둡니다.

## 4. Windows에서 연결 하나를 유지하는 방법 (비교)

A·B·D로 줄인 뒤에도 "작업 묶음당 로그인 한 번"을 원하면 연결을 유지해야 합니다. 이 PC는 Windows이고 기본 OpenSSH(`C:\Windows\System32\OpenSSH\ssh.exe` 10.3p1)와 WSL2 Ubuntu-22.04(OpenSSH 8.9p1)가 있습니다. `paramiko`·`asyncssh`는 설치돼 있지 않고 labhq 의존성에도 없습니다.

| | 장수 셸 세션 (labhq가 ssh 하나를 열고 명령을 stdin으로 보냄) | 라이브러리 (paramiko·asyncssh) | **WSL OpenSSH ControlMaster** |
|---|---|---|---|
| 로그인 수 | 세션당 1. 끊기면 재접속 1 | 연결당 1, 채널은 같은 연결 | 마스터당 1. 이후 명령은 같은 연결의 채널 |
| Windows 지원 | 됨(기본 ssh.exe, 파이프) | 됨(순수 Python) | WSL 안에서만. **Windows 기본 OpenSSH는 ControlMaster/ControlPath 다중화를 지원하지 않습니다**([Win32-OpenSSH #405](https://github.com/PowerShell/Win32-OpenSSH/issues/405), 공개 자료로만 확인, 이 PC에서 시험하지 못함) |
| 새 의존성 | 없음 | 있음(해시 잠금 의존성에 추가, 공급망 검토) | WSL 필요. labhq에는 이미 선택 기능 `setup-wsl`이 있음 |
| 명령 구분 | 직접 만들어야 함(끝 표지, 종료 코드, 오류 섞임). 하나가 멈추면 이후 명령이 막히거나 출력이 섞임 | 명령마다 채널이라 깔끔(종료 코드·stdout/stderr 분리) | 명령마다 별도 `ssh` 프로세스라 기존 `_run` 구조와 같음 |
| 실패 복구 | 직접 재접속·재동기화. 제출 도중 끊기면 qsub가 돌았는지 모름(기존 `MAYBE_SUBMITTED`와 같은 문제) | 직접 재접속. 같은 문제 | `ControlMaster=auto`면 다음 명령이 새 마스터를 열어 **로그인 1건 추가**. 마스터가 죽어도 명령은 계속 시도됨 |
| 사이트 호환 | `~/.ssh/config`(ProxyJump·Match 등)를 ssh가 그대로 처리 | **ssh 설정 의미를 다시 만들어야 함**. ProxyJump·ProxyCommand·Kerberos·하드웨어 키·ssh-agent 지원이 라이브러리와 사이트마다 다름 | `~/.ssh/config`를 ssh가 그대로 처리 |
| 2단계 인증·대화형 로그인 | 첫 로그인을 사람이 해야 하면 자동화 불가 | keyboard-interactive 처리기가 필요 | **사람이 마스터를 한 번 열고, labhq는 그 소켓만 씀. 마스터가 없으면 새 연결을 하지 않도록 fail-closed로 막아야 함**(아래) |
| 보안 | 명령이 원격 셸의 stdin으로 들어가 인용·주입 표면이 `ssh host "<명령>"`보다 넓음 | 호스트 키 정책을 코드가 정해야 함(`RejectPolicy`), 자격 증명이 프로세스 메모리에 있음 | 소켓 파일은 소유자만 열 수 있어야 함: **WSL 내부 파일시스템**(`/tmp`·`~`)에 두고 `/mnt/c`에는 두지 않기(소켓 불가), 경로 길이 제한은 `%C` 해시로 |
| 구현 크기 | 큼(프로토콜 직접 구현) | 중간(+ 사이트 호환 위험) | **작음**: `_run`의 `ssh` 앞에 `wsl.exe`와 `-o ControlPath=…`를 붙이고 마스터 확인(`-O check`)을 더함 |

**추천(근거 요약).**
1. 먼저 **A·B·C**(한 번에 묻기, 간격·backoff, 로그인 상한)와 가능한 사이트는 **D**(분리된 상태 파일, `build_script` 변경 필요)를 합니다. 코드만 바꾸고 로그인이 한두 자릿수로 줄어듭니다.
2. 그래도 연결 유지가 필요하면 **WSL ControlMaster를 사람이 연 마스터에 올라타는 방식**을 먼저 봅니다. 기본 ssh가 `~/.ssh/config`를 해석해 사이트 차이를 흡수하고, 2단계 인증 사이트에도 맞습니다. **조건: 마스터가 없을 때 새 연결을 시도하지 않아야 합니다.** `-o ControlMaster=no -o ControlPath=…`만으로는 부족합니다. 마스터가 만료되면 ssh는 새 비공유 연결로 넘어가 로그인이 생깁니다(WSL OpenSSH 8.9p1에서 마스터 없는 소켓 경로로 확인: `Control socket … does not exist` 뒤 `Connecting to <호스트>`). 같은 호출에 `-o ProxyCommand=false`를 더하면 마스터가 없을 때 `Executing proxy command: exec false`에서 멈추고 호스트에 연결하지 않습니다(같은 방법으로 확인). 명령 앞에는 `ssh -O check`를 두어 마스터가 있는지 먼저 봅니다. **확인하지 못한 것:** 살아 있는 마스터가 있을 때 `ProxyCommand=false`가 다중화를 막지 않는지(다중화 클라이언트는 프록시를 거치지 않는다고 알고 있지만 이 PC에는 접속할 sshd가 없어 시험하지 못했습니다), 사이트 설정이 `ProxyJump`일 때 명령줄 `ProxyCommand`가 이기는지.
3. 장수 셸 세션과 라이브러리는 **권하지 않습니다**. 구현이 크고(셸) 사이트 호환 위험이 큽니다(라이브러리). 라이브러리는 명령 구분이 가장 깔끔하다는 점이 유일한 장점입니다.

## 5. 이 조사로 PI의 결정이 필요한 것

- 회사 전산팀이 **무엇을 로그인 기록으로 세는지**: ssh 연결당인지, 세션(채널)당인지. 이 답에 따라 ControlMaster가 실제로 기록을 줄이는지가 달라집니다. 이 조사는 기록 방식을 확인하지 못했습니다.
- 로그인 상한 값(하루·요청당).
- 완료 표지 방식(D)을 사이트 프로필 선택으로 둘지.

## 6. 하지 않은 것·확인하지 못한 것

- 실제 HPC 접속, 로그인 수 측정, scheduler 명령 실행: 하지 않았습니다. 2절 수치는 코드에서 센 계산입니다.
- Windows 기본 OpenSSH의 다중화 미지원은 공개 이슈로만 확인했고 이 PC에서 시험하지 못했습니다(접속할 sshd가 없음).
- WSL ControlMaster가 이 PC의 WSL2에서 동작하는지, 회사 게이트웨이 앞에서 마스터가 얼마나 유지되는지는 확인하지 못했습니다.
- D(완료 표지)가 되려면 runner가 공유 FS를 실제로 읽을 수 있어야 하는데, 어느 사이트에서 그런지는 확인하지 못했습니다.
- `hpc.ssh_host`를 쓰는 runner를 HPC 안에 두는 구성(`docs/manual/08-structure.md:118`의 "HPC 로그인 노드")은 이슈가 지적한 회사 원칙과의 충돌이 있습니다. 이 조사는 그 구성을 바꾸는 일은 다루지 않았습니다.

근거 코드: `labhq/tools/scheduler.py`(`_run` 325행, `submit` 362행, `queue`/`status`/`cancel` 395~495행, `build_script` 152행), `labhq/runner/daemon.py`(`_job_watch_loop`·`_poll_jobs` 3600행 이후), `labhq/tools/hpc_mcp.py`, `labhq/settings.py`(`HpcSettings`, `job_poll_s`), `labhq/doctor.py:1140`.
