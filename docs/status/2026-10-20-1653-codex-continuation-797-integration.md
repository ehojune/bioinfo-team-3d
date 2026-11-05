Fixes #797
Refs #672, #680
PR #807: CP1 전 후보 검증·계획/승인 seal, dispatch 도구 증거와 부분 완료 결과 보존을 유지합니다.

- 최신 main `3e924495`를 merge해 HANDOFF 충돌을 해결했습니다. main의 독자별 안내·현행 큐와 #807의 context/도구/현재 차수 결과 보존 근거를 함께 남겼습니다. HANDOFF는 LF 기준 4,690→3,314자로 줄었으며 이전 검증 기록 링크를 유지합니다. manual은 #807 구현이 병합 전임을 표시합니다.
- 이번에는 제품 코드·테스트·고정 hash/계약 항목을 바꾸지 않았습니다. CP1/input-fit·승인 후 drift·부분 완료 보존·Shadow 분리 관련 mock/static **38 passed**. 실제 process/thread/proc_tree·Hub·연구/bench·PI·원자료/HPC는 실행하지 않았습니다.
- 시작 시 head `2787ef08`에서 Linux 3.10/3.12 등 완료 CI는 SUCCESS, Windows는 IN_PROGRESS였고 실패 job은 없었습니다. 요청한 `gh run view --log-failed`도 실행 중이라 로그를 제공하지 않았습니다. 실패를 추정해 코드를 고치거나 CI를 기다리지 않았습니다.
- public·diff·patch-notes·notes 검사 통과. 전체 실패/검증 artifact는 작업별 D: 임시 폴더에 보존했습니다. 최종 head CI·새 리뷰/P1=0·병합/배포는 controller 담당이며 실제 연구 효용·native/Windows 전체 실행은 별도 검증 경계입니다. #798/#811·P2·다른 owner 범위 미착수입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>