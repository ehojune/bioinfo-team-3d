Fixes #748

문법 검사와 실행 상태를 분리하는 receipt를 정리본·공통 보고서·bench manifest에 연결합니다. 기존 P1 3건은 a369a41로 수정했습니다.

- CI 회귀: 취소된 부분 보고서에 새 재현 검증 절이 붙으면서 기존 전체 문자열 assertion이 실패했습니다. fixture로 동일 실패 1건을 확인했습니다.
- 기존 SVG·부분 보고서 본문과 receipt 절을 합친 전체 문자열을 정확히 비교합니다. 연결된 JSON의 빈 코드/lock 목록·missing lock, 저장된 not_applicable/not_rerun, 원래 cancelled 상태·Result 본문도 검증합니다. 기존 SVG·외부 요청 없음 검사는 유지했습니다.
- 수정 뒤 같은 테스트와 보고서 없는 취소·검토/승인 후 mock 저장 계약까지 pytest 8 passed. 지정 lanes Python·기존 D dependency site를 사용했습니다. public·diff·patch-notes·notes 검사를 적용했습니다.
- 제품 코드·승인 게이트·공개 필터는 변경하지 않았습니다. 전체 suite·실제 process integration·PI 요청/up-down·설치·원자료·HPC·실제 분석·게시는 미실행입니다. push 후 CI 1회 snapshot만 남기고 종료합니다.
- HANDOFF 큐에 이번 회귀와 #748 병합 뒤 같은 담당 #682·Opus #754 순서를 반영했습니다. 확정 한도 7M은 유지합니다.

## 남은 지적

- P2 4315249511(Bash 선행 `-` basename)·4315630188(복수 script 공유 출력)은 이번 회귀 범위에서 수정하지 않았습니다.
- Rscript 실측·실데이터 재실행·수치 동등성은 미검증입니다. fixture PASS는 과학 결과 재현 PASS가 아닙니다.
- CI 대기·새 리뷰 호출·병합·배포는 하지 않으며 총괄 driver가 최종 CI를 확인합니다.

🤖 Generated with Codex (gpt-6.1-sol) for the labhq dev lead
