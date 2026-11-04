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

- [#835](https://github.com/ehojune/bioinfo-team-3d/pull/835)는 `0fc11e64`로 병합했다. 최종 head `0b7e54c6`의 [CI39647868669](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39647868669)는 필수 10개 job 모두 통과했다. Linux 3.10 로그에서 waiting 16건·복구 identity 2건과 attachment 17건의 실제 PASS를 확인했다. Linux 3.12 전수 job도 통과했다.
- Windows job은 6분 29초, 핵심 26건은 모두 PASS했다. 나머지는 3,345 PASS·28 skip이며 전체 수집 11,127건 중 3,399건을 선택했다. PR head `0b7e54c6`의 CI 근거이며 실제 연구 성능이나 다른 head와의 속도 개선율을 뜻하지 않는다.
- 첫 [실패 CI39642716136](https://github.com/ehojune/bioinfo-team-3d/actions/runs/39642716136)의 검사 목록·strict plan fixture 오류는 validator·승인 hash·제품 소스를 유지하며 고쳤다. 장착 검사는 승인된 upstream의 준비 시간을 명시하고 실제 DAG·변환 1회·지정 단계 장착·격리·bundle·호출 미확인 조건을 유지한다.
- 로컬 검증은 동기 순수·schema·Python 3.10 AST 범위였다. 비동기 동작의 통과 근거는 위 CI이며 로컬에서 실제 runner나 연구 요청을 실행하지 않았다.
- 실제 병렬 빌드·원격 종료·UI·시간/금액·효용과 역사적 세 번째 빌드의 원인은 미확인이다. #671은 열어 둔다.
