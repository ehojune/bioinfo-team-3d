# #671 승인 뒤 논문 스킬 준비

Lookup-only. [원 요구](https://github.com/ehojune/bioinfo-team-3d/issues/671). 실제 요청의 시간·금액·효용은 미검증이다.

| 조건 | 구현·남은 확인 |
|---|---|
| 빠른 CP1 | 기존 metadata-only 준비 유지. 승인 hash·선정·재사용 증명을 확인한 뒤 DAG 준비 시작 |
| 병렬·중복 | 승인된 descriptor마다 DAG당 한 빌드. 동시 수는 2, runner 및 DAG 병렬 한도의 최솟값. runner의 실제 슬롯·정책은 그대로 |
| 단계 시작 | 이미 완료된 결과만 즉시 snapshot. 미완료·미준비면 기다리지 않고 `unavailable/paper_skill_late`로 receipt·입력 고정. 늦게 완료돼도 같은 단계에 주입하지 않으며 이후 다른 단계는 준비된 결과를 사용 가능 |
| 빌드 수명 | 백그라운드 준비는 `runner.task_timeout_s`(기본 6시간)까지이며 단계 대기 시간이 아님. 별도 watcher가 만료 시 취소를 요청하고 기존 회당 10초 유예로 정리. 수명·금액 상향 없음 |
| 비용 | 남은 요청 예산에서 기존 per-task 예약액 × DAG 실행 슬롯 몫을 먼저 남긴다. 그 나머지·recruiter 한도·양수 per-task 한도 중 최솟값을 논문에 예약한다. 금액 상향 승인 없음. runner 변환도 task cap 유지. 미확인 취소 비용은 예약 유지, 확정 비용과 중복 합산하지 않음. 시간 제한은 금액 상한이 아니며 엔진별 실제 청구·집행은 미검증 |
| 진행 | 기존 task dispatch/title, CSO `agent.log`, 단계 `request.step_wait`에 준비 종료 수/총수·상한. 종료 수에는 실패·생략도 포함 |
| 종료·재계획 | DAG 종료/예외/요청 취소 시 기존 durable runner cancel 및 bounded waiter 정리. 취소 요청은 원격 종료 확인과 다름. 불확실한 비용은 0으로 처리하지 않음 |
| 재시작 복구 | gateway가 선택한 실제 durable ID를 recovery await 전에 알림. 취소 대상·예약을 그 ID로 연결하며 알려진/모르는 비용과 중복 합산하지 않음. 기존 request·step·round·직원 identity 매칭 기준은 유지 |
| 호출 증거 | 기존 소유권·hash·정확한 결과 참조 유지. 준비·장착·배정만으로 `called=true` 아님 |

## 세 번째 빌드: 소스로 확인한 가능성과 한계

- 기존 `selections`는 정규화 DOI/PMID 중복을 빼고 **계획당 최대 두 논문**을 고른다. 요청 전체의 평생 두 빌드라는 뜻은 아니다. 이 규칙은 바꾸지 않았다.
- 기존 변환 key에는 담당 `step_id`가 들어갔다. 동일 논문을 두 단계에 배정하면 두 변환 task가 생길 수 있었다. 이제 같은 승인 descriptor를 공유한다. hash를 검증한 talent 캐시가 재사용되면 변환 task 수와 실제 새 빌드 수는 다르다.
- 재계획은 다른 논문·선정 이유·담당 단계로 새 descriptor를 만들 수 있다. 따라서 계획별 두 편을 지켜도 요청 전체의 변환 task는 세 개 이상일 수 있다. 승인된 과학 범위를 지우거나 요청 전체 한도를 새로 만들지 않았다.
- 당시 세 번째 작업이 중복 배정·재계획·캐시 실패 중 무엇이었는지는 **UNVERIFIED**. PI DB·로그·원문을 읽지 않았으며 역사적 원인으로 단정하지 않는다.

## 검증 경계

- P1 source/pure 2건은 기준 `5fa1844e`에서 red→green. 이전 8건은 반복하지 않았다.
- 첫 [CI run39642716136](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39642716136)은 24건 실패했다. Inventory 6건은 별도 보정됐고, waiting/recovery 17건은 불완전한 fixture plan이 strict validator에서 막혀 async 동작에 도달하지 못했다. 공유 순수 builder로 필수 plan·step 필드를 채웠으며 validator는 그대로다.
- 기존 attachment 1건은 준비 시간 없이 즉시 장착을 기대했다. 승인된 upstream 단계가 fake 준비 시간을 제공하도록 바꾸고, 실제 DAG·변환 1회·지정 단계 장착·scope 격리·bundle·호출 미확인 검사를 유지한다. 승인 예산에 정상 두 슬롯과 선택적 한 턴을 포함하고 선언 산출물도 fixture가 쓴다.
- 로컬은 기존 fixture의 16/24/32 schema 오류 재현→4형태 검증·hash 일치, 새 동기 순수 2건, Python 3.10 AST/compile 4파일만 확인했다. Async 18건의 통과는 새 CI가 필요하다. 제품 코드는 바꾸지 않았다.
- 실제 병렬 빌드·원격 종료·UI·시간/금액·효용과 역사적 세 번째 빌드의 원인은 미확인이다. #671은 열어 둔다.
