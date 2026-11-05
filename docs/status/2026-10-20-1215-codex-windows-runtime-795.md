Windows detached launch에 BREAKAWAY를 요청하고 자식의 모든 Job 비소속 확인 뒤 PID를 기록합니다. 거부·잔류·unknown은 성공 기록 없이 실패하며 foreground/POSIX·identity guard를 유지합니다. 설정 준비와 runtime 준비를 구분하고 stale/missing/unknown managed daemon을 실패로 판정합니다. Fixes #795.

후속 수정 `45240fb9`: 읽기 전용 health 검증에 필수 `started_here=False`를 전달합니다. 실제 check_health와 autospec으로 정상·타 identity·identity 없음, remote/foreground·WSL을 검증합니다. Job 조회는 pointer/BOOL 규격을 확인하고 native/binding 오류와 잔류를 출력합니다. Windows CI positive는 launch 성공→부모 Job close→child 생존→소유 handle 정리를 요구하며 거부/unknown을 PASS로 받아들이지 않습니다. denied는 별도 test입니다.

검증: 로컬 mock/static **107 passed**, guard 3개 유지·blocked 0. 수정 전 health 5건 TypeError 재현 로그와 수정 뒤 전체 출력을 보존했습니다. Python 3.10 AST(embedded CI script 포함)·public·diff·기록 검사 PASS. 실제 process/thread/signal/Job fixture는 로컬에서 실행하지 않았습니다.

한계: [완료된 clean-install CI](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39361662285/job/118429060176)는 launch exit 2로 실패했습니다. 이전 출력에는 child membership/native 오류가 없어 원인은 미확정이며 **이 회귀가 해결됐다는 근거는 아직 없습니다**. 실제 생존·새 head CI는 root 확인 대상입니다. 배포 gate는 `doctor --runtime` exit 0 및 `runtime_readiness.ready=true`입니다. PI 상태 변경·배포·봇 호출·병합은 하지 않았습니다.

manual 166,205→166,295자, reference 1,811→3,029자(LF Unicode). [근거·남은 CI 확인](docs/reference/windows-runtime-795.md). workflow·host Job 정책·broker/service 변경은 없습니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
