## #671 — 단계 시작 snapshot·복구 ID P1 보정

- Ready-step은 완료된 스킬만 snapshot하고 미완료면 즉시 생략한다. 같은 단계는 나중 완료돼도 바뀌지 않으며 백그라운드 빌드의 기존 수명·취소 한도는 별도로 유지한다. 6시간을 단계 대기 시간으로 쓰지 않는다.
- Gateway가 recovery await 전에 고른 durable ID에 취소·예약을 연결한다. 복구 결과/미확인 비용과 중복 계상하지 않으며 취소 요청을 종료 증거로 보지 않는다.
- 새 동기 source/pure P1 2건은 기준 `5fa1844e` red→green. 이전 8건은 반복하지 않았다. Python 3.10 AST/compile 8파일, DAG·선정/호출·금액 정책·gateway recovery 14함수 AST 동일 확인.
- Async fake는 CI용이다. 실제 빌드·UI·청구·원격 종료와 당시 세 번째 빌드의 원인은 미확인이다. 두 편은 기존처럼 계획당 선정 상한이며 #671을 닫지 않는다.
- manual 176,703→176,731자(+28). [조건·근거](../reference/paper-skill-waiting-671.md). 다른 owner의 Node/CI 파일은 바꾸지 않는다.
