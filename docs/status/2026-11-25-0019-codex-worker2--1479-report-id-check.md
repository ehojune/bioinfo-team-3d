# #1492 · 보고서 식별자 검사

#1492 분할 1/2. 기존 [#1493](https://github.com/ehojune/bioinfo-team-3d/pull/1493)은 hold 상태입니다.

- `labhq verify`가 실시간 출처 확인 설정에서 본문·과학 부록·원장의 HGNC 기호, GO ID, DOI를 조회합니다. 모델 호출은 없습니다.
- 등록 기관이 확인한 미등록과 통신 오류·조회 한도를 구분합니다. 결과는 verify JSON과 감사 번들의 `claims.json`에 남습니다.
- 쉼표로 나열한 HGNC 기호를 모두 조회합니다. 두 번째 이후 기호의 등록과 미등록을 회귀시험으로 확인했습니다. [리뷰 P2](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682313050)
- 보고서·verify 대상 시험 157 passed. 공개 저장소 점검 통과.
- 제품 순증 +201줄. `cso.py` 0, `server.py` 0, `daemon.py` 0. 핵심 파일 누계 +18줄, 잔여 82줄.
