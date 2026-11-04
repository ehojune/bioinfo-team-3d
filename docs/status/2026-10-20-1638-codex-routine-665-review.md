Fixes #665
PR #791 · 리뷰 수정 `7ec4a59`.

- Codex 입력 snapshot은 POSIX/Windows·resume에 같은 native `:read-only` 기반 profile을 적용하며 `outputs/`·`.tmp/`만 씁니다. 추가 write root·사용자 config·extra CLI args로 넓히지 않고, 작업 폴더를 재사용하는 후속 turn도 입력을 보호합니다. 실제 sandbox 실행 검증은 별도입니다.
- 조상 metadata에 결과 task id를 전달해 첫 실패 run 대신 해당 성공 retry run을 검사합니다. manifest의 요청·step·성공 상태·파일 hash와 최신 per-run identity 검사를 유지합니다.
- 거부된 조상의 부분 복사·이전 snapshot은 inventory 소유 파일만 지웁니다. 원본·알 수 없는 파일은 보존하고 정리 실패는 직원 실행 전에 중단합니다. 선언 단계의 수집 hash 상한은 input의 파일/총량 허용 상한까지 보장합니다.
- 원래 head에서 보강 회귀 **14 failed** → 관련 mock/static **67 passed, 1 skipped**. 성공 retry·누락/다른 run·부분 실패/정리 실패·큰 입력 허용과 상한 거절·profile 구성·DAG task binding을 검사했습니다. 기존 hash cap/growth assertion은 유지하고 fixture의 output/input cap을 함께 고정했습니다.
- 지적 4320685868은 기존 head의 patch-notes 검사 통과와 현재 entry를 확인해 stale 근거로 답글을 남깁니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 최신 main #810 변경을 merge로 보존했습니다. 실패 traceback은 작업별 local artifact에 보존했고 mock guard blocked는 0건입니다.
- 실제 process/thread/proc_tree·직원 CLI·PI·원자료/HPC·연구/bench·설치·게시·배포 미실행. 권한 fixture skip·전체 suite·native sandbox·Windows/3.10 실제 실행은 CI/별도 승인 경계입니다. 기존 SyntaxWarning은 범위 밖이며 P2·다른 owner 변경은 건드리지 않았습니다. 최종 head CI·새 리뷰·P1=0·병합은 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>