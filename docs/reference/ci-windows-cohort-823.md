# Windows cohort 후속 (#823 · 미게시)

명시한 OS 경계와 변경 test를 선택하는 제안입니다. Windows full과 동등하지 않으며 실제 속도는 미측정입니다.

| 증거 | 값 |
|---|---|
| 기존 PR Windows job 118911719909 | 4 workers, 10,461 전수, 10,413 PASS/48 skip, pytest 1,427.53초 |
| main 39510024326 보존 artifact | pytest 1,536.07초, lock 대기 합 2,949.22 worker-seconds/77파일 |
| 이전 로컬 선택 | 9,270/10,554개(87.8%), 272파일 |
| 이번 정적 선택 | 4,531/10,578개(42.8%), 169파일; 6,047개 제외 |
| 이번 보호 범위 | 명시 cohort 3,812 + 혼합 파일 경계 15 + native API backstop 704 |
| host lock 정적 판정 | 22파일/456노드 → 23파일/139노드; durable_writer 28노드는 lock 불필요 |

**30%·2–3천 개 목표는 미달입니다.** 이번 검토가 유지한 명시 경계만 3,827개(36.2%)입니다. 모든 가능한 정책의 최솟값을 증명한 수치는 아닙니다. 설치 경로 497개, 경로 policy 256개, private path 254개, shell quoting 217개와 process/Job/ACK·receipt·SQLite·CLI/MCP 경계를 더 줄일 근거는 이번 범위에서 확보하지 못했습니다.

- [profile](../../config/ci/windows-boundary-v1.json)은 schema 3 / `windows-boundary-v3`입니다. `known_tests`는 collection 목록이며 순수성 인증이 아닙니다. 경계 cohort·native API·PR/push 변경 test를 포함합니다. 일반 schema/prose/logic 파일마다 제외 승인을 요구하지 않습니다. 새 파일·미확정 node는 포함합니다.
- API backstop은 test와 참조한 test helper/fixture, import-time 코드를 읽습니다. 클래스는 전체를 남깁니다. 미사용 native sibling 때문에 순수 node를 모두 포함하지 않으며 별칭·autouse·usefixtures도 검사합니다. 임의 제품 구현의 완전한 동적 call graph 증명은 아닙니다.
- 혼합 파일은 bulk pack 128→1(UTF-8), environment signatures 102→10(CLI/adapter), evidence verify 79→2(separator/case), semantics objects 18→2(경로 가림·동일성)로 좁혔습니다. source digest가 다르거나 scope를 해석하지 못하면 파일 전체를 포함합니다. Linux 전수와 Windows nightly/manual 전수는 유지합니다.
- checkout과 PR ordered base/head 또는 push before/after·ancestor가 맞아야 변경 range를 인정합니다. 증거 누락·불일치와 공유 test helper/fixture data·policy/dependency 변경은 전수입니다. 제품 변경 하나로 전역 runtime fingerprint가 만료되지는 않습니다.
- host lock은 node와 사용 fixture/helper의 setup~teardown을 감쌉니다. 고정·미확정 listener와 global snapshot, `watch_gateway_exit → cli._terminate_tree → proc_tree.kill_tree`를 보호합니다. 지역 Settings에 포트 0을 한 번 무조건 지정하고 생성 전 escape/rebind가 없음을 확인한 Runner만 broker 전역 lock에서 제외합니다.
- manifest/artifact v5는 v3 profile과 기존 tree/workflow/policy/run/attempt/digest/ordered parents·성공 gate 조건을 묶습니다. main before=검증 PR base도 유지합니다. v4 manifest/v2 profile은 재사용하지 않습니다.

guarded mock/static 415 PASS. 바뀐 CI 계약 175개만 재수집하고 나머지는 직전 collection을 재사용했습니다. 제품 test는 변경하지 않았습니다. 실제 Windows·xdist·경합·대기 절감·5분 달성은 미검증입니다. 보존 main 대기 합과 전달받은 2,450초의 차이 원인도 미확정입니다.

정책 변경 PR의 첫 실행은 전수 fallback입니다. 게시 뒤 유효한 일반 PR/push range에서 경계 실행의 full/selected 목록·profile·worker 수·setup/pytest/queue 시간·lock 대기를 artifact로 확인해야 합니다. static 예상치로 실행 성능을 대신하지 않습니다. 게시 전 총괄의 #824 이후 main 통합·exact-head 검토가 남습니다.
