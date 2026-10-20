Fixes #797
Refs #672, #680
PR #807: P1 4323683886 — paper-skill 방법이 바뀌면 이전 결과를 재사용하지 않도록 수정했습니다.

- 해당 단계의 `paper_skills` selection과 step/agent에 연결된 `paper_skill_results` receipt 전체·순서를 context hash에 넣습니다. CP1 후보 identity와 실행 context가 같은 hash를 쓰며 prompt에는 receipt 경로/값을 추가하지 않습니다.
- 방법 변경은 그 단계와 의존 단계의 재사용을 거절하고 다른 단계의 별도 skill 변경은 독립 후보를 유지합니다. 구 실행 signature는 새 계약의 증거로 쓰지 않습니다. 승인 뒤 현재/보관 plan의 해당 입력이 바뀌면 중단·새 CP1을 요구하며 조용히 재실행하지 않습니다.
- 기존 head 새 회귀 15건 red 뒤 새 18건을 포함한 관련 mock/static **129 passed**. 선언·실제 attachment metadata 대조, CP1 후보/snapshot·승인 후 변경·기존 요청 입력 차단·재개/예산 rollback을 확인했습니다. Python 3.10 AST·public·diff·patch-notes·notes 검사 근거를 기록했습니다.
- Paper2Skill 변환/설치·실제 source bytes 읽기·process/thread/Hub 운영·PI·연구/bench/HPC/원자료는 미실행입니다. 기존 hash/path·승인·보안·bounded loop를 유지하고 실행 확장·P2·다른 owner 범위는 건드리지 않았습니다. 최종 head CI·새 리뷰/P1=0·병합/배포는 controller 담당입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
