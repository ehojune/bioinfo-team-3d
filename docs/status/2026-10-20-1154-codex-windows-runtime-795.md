PR #802

## 변경

Windows detached 실행은 CREATE_BREAKAWAY_FROM_JOB을 요청하고 소유한 자식 handle의 전체 Job 비소속을 확인한 뒤 PID를 기록합니다. 거부·잔류·unknown은 성공 PID/readiness 없이 실패하며 안전하지 않은 flag 재시도는 없습니다. 호출자 Job membership·immediate limits와 launch 단계를 기록합니다. lifecycle exit 기록 부재만으로 과거 Job 강제 종료 원인을 확정하지 않습니다.

설정 준비와 runtime 준비를 분리하고 `doctor --runtime`을 배포 점검 계약으로 추가합니다. 과거 managed daemon의 stale/missing/unknown 신원은 실패, pristine·정상 down은 별도 상태입니다. remote/foreground는 로컬 PID 없이 인증된 gateway·runner로 확인하며 켜진 WSL runner 연결도 확인합니다. foreground/POSIX·PID identity guard는 유지합니다.

Fixes #795

## 검증·한계

- 로컬: guard 환경을 유지한 mock/static 100 passed, blocked 0. Python 3.10 AST(실제 CI subprocess script 문자열 포함)·public·diff 검사 통과. 실제 process/thread/signal/Job fixture는 실행하지 않았습니다.
- CI 전용 Windows 테스트: 임시 fake daemon·state·소유 kill-on-close Job의 breakaway 허용/거부, Job close 뒤 생존 또는 명시적 실패, PID/readiness 부재와 생성 시각을 확인한 handle의 정리. 상위 CI Job이 허용 경로를 막으면 명시적 실패를 출력하며 이 경우 생존은 미검증입니다. 실제 CI 결과는 아직 확인 전입니다.
- manual 165,668→166,205자. 상세 근거·Microsoft 문서: docs/reference/windows-runtime-795.md. workflow·CI 정책은 변경하지 않았습니다.
- 실제 PI state/daemon/Job·기관·연구·HPC·raw data는 건드리지 않았습니다. CI·fresh-head 리뷰·배포·병합은 root 담당이며 breakaway 거부 시 독립 콘솔이 필요합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
