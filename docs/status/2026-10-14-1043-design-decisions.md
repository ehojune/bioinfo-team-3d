PR #582 · `design-decisions` · Refs #546

- 결정 배지(0건 숨김), 질문·사실·설명·48px 승인·거절·메모 순서, 원문 펼침을 2D에 적용했다.
- 검토·지난 결정은 같은 부품을 쓰며 시각·보고서·× 숨김을 유지한다. 로그인 대기는 점선 운영 카드로 구분한다.
- 새 Node 8건 main 실패→branch 통과, 전체 Node 48개 파일 통과. 밝은/어두운 주요 토큰 대비 최저 5.10:1/6.39:1, 브라우저 실제 20쌍 최저 5.85:1.
- 1440×900·390×844 전후 화면 첨부. 폰 첫 카드 1,390→163px, 가로 넘침 없음.
- Windows 전체: **6,272 passed·66 skipped·2 failed**. 화면 밖 manifest 교체 접근 거부·shadow 제거 검사 실패. 임시 폴더 삭제 확인. 다른 레인 pytest 실행으로 추가 전체 검사는 하지 않았다.
- CI: head 1d9a24d의 [run 38497285439](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38497285439)에서 changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 건너뜀. 로컬 Windows 전체 PASS는 미충족이다. 봇 리뷰·병합은 총괄에게 넘긴다.

[화면·회귀 근거](../design/2026-10-13/docs/reference/decisions-verification.md)
