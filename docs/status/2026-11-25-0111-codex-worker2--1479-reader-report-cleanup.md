# #1492 · 독자용 보고서 정리

Closes #1492

#1492 분할 2/2. ID 검사 [#1494](https://github.com/ehojune/bioinfo-team-3d/pull/1494) 병합 뒤의 변경입니다.

- 새 보고서 본문과 과학 부록에서 로컬 경로를 가립니다. 절대경로 링크는 표시 문구를 남기고, 산출물 등록 경고는 한글로 표시합니다.
- 공백이 있고 확장자가 없는 경로 뒤에 구두점 없이 이어진 결론과 근거 표식을 검증했습니다. 저장 문서와 독자 화면, 기존 보고서의 요약에서 확인했습니다. [기존 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1493#discussion_r4682132659)
- 주어로 시작하는 영문 결론도 보존합니다. 경계가 모호한 경로는 새 보고서 작성 지시문에서 백틱으로 감싸도록 했습니다. [리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4682984865)
- POSIX 사용자 홈 축약과 한 구성요소짜리 절대경로도 본문과 링크에서 가립니다. [경로 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683086011)
- 영문 대명사·한정사와 국문 서술형 어미를 경계 판단에 쓰고, Windows device·확장 UNC 경로를 가립니다. [결론 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683163965) · [Windows 경로 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683163982)
- POSIX 경로 안의 콜론도 가리고 끝에 붙은 근거 줄 범위는 남깁니다. [콜론 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683244361)
- 취소 보고서도 저장 전에 경로를 가립니다. 저장본과 완료 이벤트에서 경로가 가려지고 결론과 근거 표식이 남는지 확인했습니다. [취소 보고서 리뷰 P1](https://github.com/ehojune/bioinfo-team-3d/pull/1496#discussion_r4683333586)
- 취소·보고서·PDF·재현 묶음 대상 시험 282 passed, 2 skipped. 공개 저장소 점검 통과.
- 제품 순증 +90줄·4커밋. 두 PR 제품 순증 합계 +300줄. `cso.py` 0, `server.py` +1, `daemon.py` 0. 핵심 파일 누계 +19줄, 잔여 81줄.
