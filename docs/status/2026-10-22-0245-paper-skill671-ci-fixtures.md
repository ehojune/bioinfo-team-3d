## #835 / #671 — 첫 CI의 fixture 계약 보정

- Waiting/recovery 17건의 필수 plan·step 누락을 공유 순수 builder로 고쳤다. Strict ResearchPlan·승인 hash·제품 동작은 유지한다.
- Attachment 검사는 승인된 upstream 단계의 준비 시간 뒤 실제 DAG 변환 1회·장착·격리·bundle을 확인한다. Ready-step 대기나 늦은 주입을 되살리지 않는다.
- 로컬: 원 fixture 16/24/32 schema 오류→4형태 검증·hash 일치, 새 동기 순수 2 PASS, Python 3.10 AST/compile 4파일. Async·runner 실행은 하지 않았으며 18건의 green은 새 CI가 필요하다.
- Root의 inventory 6건 보정·config는 보존했다. 실제 빌드·비용/효용·진행 화면·원격 종료·세 번째 빌드 원인은 미확인이고 #671을 닫지 않는다. [근거](../reference/paper-skill-waiting-671.md).
