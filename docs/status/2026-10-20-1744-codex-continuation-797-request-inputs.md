Fixes #797
Refs #672, #680
PR #807: P1 4323035753 — 요청 단위 외부 입력이 재사용 증거에서 빠지던 문제를 수정했습니다(`04fbc087`).

- `references`·`project_dirs`는 모든 step에 전달되므로 내용 증거가 없으면 CP1 전에 재사용 후보에서 제외합니다. 외부 입력을 열거나 hash를 추정하지 않습니다.
- dispatch에서 입력 부재만 기록해 retry/repair·archive·재승인에 보존합니다. 기록 없는 과거 결과도 제외합니다. metadata 삭제로 미검증 입력 이력을 지우지 못하며, 승인 후 입력 추가는 중단·새 CP1을 요구합니다. 새 기록·snapshot에 입력 경로/값을 넣지 않습니다.
- 기존 head에서 새 mock 회귀 20건 red, 수정 뒤 새 23건을 포함한 관련 mock/static **112 passed**. CP1/input-fit·재개·예산 rollback을 확인했습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 통과. 전체 red/green 로그는 로컬 D: 산출에 보존했습니다.
- 실제 process/thread/Hub 운영·PI·연구/bench·원자료/HPC는 미실행입니다. 기존 승인·보안 경계를 유지하며 #798/#811·P2·다른 owner 범위는 건드리지 않았습니다. 최종 head CI·리뷰/P1=0·병합/배포는 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
