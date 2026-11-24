# #1492 · 보고서 식별자 검사

#1492 분할 1/2. 기존 [#1493](https://github.com/ehojune/bioinfo-team-3d/pull/1493)은 hold 상태입니다.

- `labhq verify`가 실시간 출처 확인 설정에서 본문·과학 부록·원장의 HGNC 기호, GO ID, DOI를 조회합니다. 모델 호출은 없습니다.
- 등록 기관이 확인한 미등록과 통신 오류·조회 한도를 구분합니다. 결과는 verify JSON과 감사 번들의 `claims.json`에 남습니다.
- 쉼표로 나열한 HGNC 기호를 모두 조회합니다. 두 번째 이후 기호의 등록과 미등록을 회귀시험으로 확인했습니다. [리뷰 P2](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682313050)
- 밑줄이 든 대체 좌위 기호와 `orf` 기호를 조회합니다. 근거 출처 조회와 ID 조회는 시간 한도를 공유합니다. [HGNC 지침](https://hgnc.genenames.org/about/guidelines/) · [기호 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682396850) · [시간 한도 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682396867)
- Markdown으로 강조한 HGNC 라벨과 기호를 읽고, 설정한 API 연락처를 식별자 조회에도 적용합니다. [라벨 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682511295) · [기호 강조 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682654172) · [연락처 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682511309)
- 조회기 내부 오류도 미확인으로 남깁니다. SQLite 허용량 기록 실패를 회귀시험으로 확인했습니다. [오류 처리 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682573000)
- 이전 보고서도 과학 부록만 조회합니다. 부록에 남은 실행 기록 속 식별자는 조회에서 제외합니다. [실행 기록 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1494#discussion_r4682739262)
- 보고서·verify 대상 시험 159 passed. 공개 저장소 점검 통과.
- 제품 순증 +210줄. `cso.py` 0, `server.py` 0, `daemon.py` 0. 핵심 파일 누계 +18줄, 잔여 82줄.
