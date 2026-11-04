Windows detached launch와 설정/runtime 준비를 분리합니다. `started_here=False`의 실제 health 함수·autospec 회귀, auth/identity·ANY-Job gate, foreground/POSIX를 유지합니다. Fixes #795.

후속: direct를 먼저 시도하고 확인된 WinError 5 또는 Job 잔류에만 소유 handle로 자식 종료·회수를 마친 뒤 transient local WMI를 한 번 시도합니다. base interpreter와 venv carrier로 redirector의 별도 PID/Job을 피합니다. nonce receipt·보유 PID handle의 생성 시각/실행 파일/current-user·모든 Job 비소속 확인 전에는 ACK하지 않습니다. bootstrap은 ACK 뒤 같은 PID로 supervisor에 진입하며 caller 소멸/만료에는 self-abort합니다. unknown·정리 실패·provider 거부에 unsafe retry는 없습니다.

환경 값은 PowerShell stdin/Startup.EnvironmentVariables 메모리로 전달하며 파일·argv·진단에는 쓰지 않습니다. 임시 control은 instance state의 current-user-only DACL과 만료를 갖습니다. site/PYTHONPATH 없이 bootstrap하고 기존 path/prefix를 복구하나 초기 isolated/no-site flags는 유지됩니다. 서비스·Task Scheduler·설치·승격·host 정책 변경은 없습니다.

검증: 로컬 mock/static **146 passed**, guard 3개 유지·blocked 0. direct cleanup/one attempt, WMI codes, nonce/creation/image/owner·timeout·unknown, native identity reader와 DACL, 같은 PID 전환을 fake로 확인했습니다. Python 3.10 AST(embedded CI scripts 포함)·public·diff·patch-notes·notes 검사 PASS. 로컬 실제 process/thread/signal/WMI·PI 상태 접근은 없습니다.

CI-only 증거 pending: nested outer non-breakaway/inner silent-breakaway에서 WMI launch 성공 후 부모 Job들을 닫고 fake supervisor와 child role의 생존·소유 handle cleanup을 요구합니다. 별도 실제 bootstrap unknown/timeout은 ACK/role 진입을 막습니다. 실제 direct 거부+mock provider 거부는 별도이며 survival PASS로 세지 않습니다. [0bf4c97d clean-install 실패](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39363311454/job/118434304160)의 child_in_job=True는 실제 잔류이며 ancestor flags는 unknown입니다. **이번 head의 실제 탈출·clean-install 해결은 아직 미검증**이고 WMI provider도 차단할 수 있습니다. CI·리뷰·병합은 root 담당입니다.

manual 166,295→166,466자, reference 3,029→3,010자(LF Unicode). [근거·제약](docs/reference/windows-runtime-795.md). 배포는 하지 않았습니다. root는 doctor --runtime rc0/runtime_readiness.ready, 실제 WSL, 명시적 idle0·global deploy lock을 별도로 확인해야 합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
