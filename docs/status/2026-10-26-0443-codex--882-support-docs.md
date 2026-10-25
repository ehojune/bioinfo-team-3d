Refs #882. HANDOFF 현재 큐·manual 지원/복구 조건과 AGENTS 역할 안내를 현재 인계에 맞췄습니다.

- #966은 `929eded9`로 병합됐습니다. #965는 #972 OPEN으로 미완료이며 #606 전체 완료·실제 운영 복구는 미검증입니다. #967 소유권 P1 해소·port P2 보류, #937/#968 취소 회귀·#920 여유 검사 병합도 구분했습니다.
- [후속 배정](https://github.com/ehojune/bioinfo-team-3d/pull/970#issuecomment-6403265512)에 따라 #966 상태를 정정했습니다. #971→#972→#969 병합 뒤 main을 반영해 manual을 최종 통합합니다. 선행 작업과 최종 head CI·동일 head 리뷰 완료·모든 head 미해결 P1=0 전까지 `hold`를 유지합니다.
- 이번 정정: HANDOFF 4,239→4,326자(+87), manual 193,672→193,752자(+80). 최초 대비 두 문서 합계 +1,754자, AGENTS 2,688→2,668자(-20)입니다. 상세 근거는 기존 reference에 연결했습니다.
- 링크·public·diff·patch-notes·notes 검사 PASS. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다. 기존 실패 head의 CI는 재실행하지 않습니다.
- 실제 transport·OS 재시작·새 설치 요청·반복 복구는 운영 미검증입니다. #882 OPEN·별도 실행 GO를 유지합니다. 코드·설정·설치·배포·연구·HPC 변경/실행은 없습니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
