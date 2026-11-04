## PR #802 P1 후속

4321008909는 기존 45240fb9의 started_here=False와 실제 함수/autospec 회귀로 stale입니다. 나머지 세 건을 수정했습니다: post-spawn 로그 실패도 소유 handle cleanup을 실행하고, PID 기록 없는 과거 이력은 현재 인증·runner 연결이 확인된 경우만 해소하며, Windows role·supervisor는 현재 PID 생성 시각·생존·ANY-Job 비소속을 조회합니다. unknown·stale·조회 오류는 실패하고 WMI 신원 미확정 PID에는 종료 권한을 부여하지 않습니다.

수정 전 새 회귀 20 failed/6 passed, 수정 후 관련 mock/static 191 passed. guard 유지·blocked 0, Python 3.10 AST 5파일·encoding 검사 PASS. 실제 Native/WMI/process/thread/signal·PI·연구·HPC는 로컬 미실행입니다. 기존 positive/nested CI gate는 그대로입니다.

manual 172,387→172,624자, reference 4,357→5,000자(LF Unicode). 근거 docs/reference/windows-runtime-795.md. push 뒤 CI를 한 번 조회하고 총괄에게 인계하며 봇 요청·대기·병합·배포는 하지 않습니다.
