# Windows cohort 후속 (#823 · 미게시)

이 branch의 제안입니다. 현재 main의 v1 결과와 새 구현의 정적 예상치를 구분합니다.

| 증거 | 값 |
|---|---|
| 기존 PR Windows job 118911719909 | 4 workers, 10,461 전수, 10,413 PASS/48 skip, pytest 1,427.53초 |
| main run 39510024326 보존 artifact | 4 workers, 10,461 전수, pytest 1,536.07초, lock 대기 합 2,949.22 worker-seconds/77파일 |
| 새 branch 직렬 collection + AST | 10,554개 중 9,270개(87.8%)/272파일 선택, 1,284개/38파일 제외 |
| 같은 source의 lock 판정 비교 | 77파일/2,625노드 → 22파일/456노드 |

- [profile 원본](../../config/ci/windows-boundary-v1.json)은 schema 2 / `windows-boundary-v2`입니다. 파일명은 유지합니다. 명시한 OS·안전 cohort와 신규·변경·미분류 test는 포함하며, 검토한 중립 계약의 source/helper digest가 같을 때만 제외합니다. 전역 runtime hash 만료는 제거합니다.
- event range는 실제 checkout SHA와 PR의 순서 있는 base/head 부모 또는 push before/after·ancestor로 검증합니다. 증거 누락·불일치·공유 fixture/policy/dependency 변경은 전수입니다. `changes.json`과 full/selected 목록은 CI artifact에 남습니다.
- manifest/artifact는 v4입니다. 기존 tree/workflow/policy/run/attempt/digest/gate 조건에 더해 main before가 검증 PR base와 같아야 재사용합니다. 같은 base와 tree가 비교할 변경 집합도 묶습니다. 구형 v1 profile/v3 manifest는 재사용하지 않습니다.
- 전용 subprocess, 임의 포트, 사용하지 않는 helper 정의는 전역 lock 근거가 아닙니다. 고정·미확정 listener 포트, 전역 process scan, 미확정 Runner broker 시작과 참조된 관련 helper는 setup~teardown을 보호합니다. import-time 실행·별칭·fixture 인자도 검사합니다. 설정 객체 생성과 실제 serve 시작은 구분합니다. Win32 table을 읽는 proc_tree/adapter snapshot wrapper의 검증한 진입점도 명시해 보호합니다.
- **선택 목표 20–30% 미달.** 아직 검토하지 않은 파일을 제외하지 않았습니다. 더 줄이려면 cohort/node별 추가 검토가 필요합니다. 제외한 계약의 제품 구현이 OS에 민감해질 위험은 남으며 Linux 전수·Windows nightly/manual 전수를 유지합니다. 정적 분석은 완전한 동적 call-graph 증명이 아닙니다.

565개 guarded mock/static PASS. 제품 node 집합은 기존 artifact와 동일합니다. CI 계약은 v2에 맞춰 갱신했습니다. 실제 Windows/xdist·경합·속도는 새 CI 전까지 미검증입니다. 기존 2,450초 보고와 보존 artifact 합계의 차이는 미확정입니다. push/PR/CI 조회는 하지 않았으며, 게시 전 총괄의 최신 main 통합·검토가 남습니다.
