Refs #882. HANDOFF 현재 큐·manual 지원/복구 조건과 AGENTS 역할 안내를 현재 인계에 맞췄습니다.

- #966은 `929eded9`로 병합됐습니다. #965는 #972 OPEN으로 미완료이며 #606 전체 완료·실제 운영 복구는 미검증입니다. #967 소유권 P1 해소·port P2 보류, #937/#968 취소 회귀·#920 여유 검사 병합도 구분했습니다.
- [추가 배정](https://github.com/ehojune/bioinfo-team-3d/pull/970#issuecomment-6404830328)의 P1 두 건을 수정했습니다. pi-qa·manual의 현재 창구는 #883, CLAUDE·운영 기준의 담당·위임·병합 판단과 pi-qa의 한도 안내는 최신 PI 인계를 따릅니다. #760의 날짜별 결정은 보존했습니다. #973→#972→[#653 snapshot 후속](https://github.com/ehojune/bioinfo-team-3d/issues/653#issuecomment-6403355901) 뒤 manual 최종 통합과 최종 head CI·동일 head 리뷰 완료·모든 head 미해결 P1=0 전까지 `hold`입니다.
- 이번 정정(LF 기준): CLAUDE 1,792→1,858자, 운영 기준 1,142→1,193자, pi-qa 7,802→7,815자, manual 193,752→193,752자. 합계 +130자이며 manual은 창구 링크 한 곳만 변경했습니다.
- 현행 안내 검색·변경 링크 16개·manual 단일 링크 변경·UTF-8·public·diff·patch-notes·notes 검사 PASS. 새 head CI·봇 리뷰는 별도 확인합니다. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다. 기존 CI 재실행·신원 변경은 중단 상태입니다.
- 실제 transport·OS 재시작·새 설치 요청·반복 복구는 운영 미검증입니다. #882 OPEN·별도 실행 GO를 유지합니다. 코드·설정·설치·배포·연구·HPC 변경/실행은 없습니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
