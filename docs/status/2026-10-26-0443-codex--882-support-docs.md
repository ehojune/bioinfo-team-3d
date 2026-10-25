Refs #882. HANDOFF 현재 큐·manual 지원/복구 조건과 AGENTS 역할 안내를 현재 인계에 맞췄습니다.

- #966은 `929eded9`로 병합됐습니다. #965는 #972 OPEN으로 미완료이며 #606 전체 완료·실제 운영 복구는 미검증입니다. #967 소유권 P1 해소·port P2 보류, #937/#968 취소 회귀·#920 여유 검사 병합도 구분했습니다.
- [추가 배정](https://github.com/ehojune/bioinfo-team-3d/pull/970#issuecomment-6404636958)에 따라 AGENTS·HANDOFF의 현재 PI 창구를 #883으로 정정하고 #760은 과거 결정으로 구분했습니다. #969 본 수정은 병합됐고 [#653 snapshot 후속](https://github.com/ehojune/bioinfo-team-3d/issues/653#issuecomment-6403355901)은 열려 있습니다. #973→#972→#653 뒤 manual 통합과 최종 head CI·동일 head 리뷰 완료·모든 head 미해결 P1=0 전까지 `hold`를 유지합니다.
- 이번 정정(LF 기준): AGENTS 2,668→2,668자, HANDOFF 4,506→4,603자(+97). manual 193,752자는 그대로입니다. 상세 근거는 기존 reference에 연결했습니다.
- 링크·public·diff·patch-notes·notes 검사 PASS. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다. 기존 실패 head의 CI는 재실행하지 않습니다.
- 실제 transport·OS 재시작·새 설치 요청·반복 복구는 운영 미검증입니다. #882 OPEN·별도 실행 GO를 유지합니다. 코드·설정·설치·배포·연구·HPC 변경/실행은 없습니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
