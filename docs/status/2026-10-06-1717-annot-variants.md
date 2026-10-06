## 2026-10-06 · #435 C ② — 변이 주석 MCP labhq_annot (#449)

- 결론: 직원이 VEP·gnomAD·ClinVar를 내장 도구로 조회한다. 결과마다 DB 판본이 붙고, 작업 폴더에 조회 기록과 응답 전체가 남는다. 통제 구역에서 나온 변이도 경고·승인 없이 조회한다(PI 결정 10-06).
- 바뀐 것: `labhq/tools/annot.py`·`annot_mcp.py`, runner 배선(`auto_approve`), analyst·bioinfo-agent·biologist·lit_scout의 `builtin_mcp`, CSO roster·capability card, 연결된 도구 표·배지, `public_resources.tsv` use 문구, manual "공개 자원 조회 도구" 절. 기존 test 기대값 두 곳(bioinfo-agent `builtin_mcp`, README 배지 문구)은 이 기능이 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot.py` 20 passed(HTTP 전부 가짜), 관련 test 13개 파일 441 passed·13 skipped, 전체 suite 4024 passed·54 skipped(Windows). test 밖에서 공개 변이로 세 API를 한 번 실측해 VCV 검색어와 gnomAD 인구 요약을 고쳤다. `scripts/check_public.sh` 통과.
- 리뷰 반영(6a43504): ClinVar가 VCF indel을 padding base가 아닌 구간으로 찾는다(BRCA1 c.68_69del을 놓치던 P1). canonical SPDI와 repeat를 감안해 비교하고, 같은 allele이 없으면 `exact_match: false`를 붙인다. gnomAD chrM은 `mitochondrial_variant`로 묻고, gnomAD 캐시는 30일 뒤 만료된다. 결과 파일 이름에 내용 hash를 넣어 같은 요청을 반복해도 덮어쓰지 않는다. 공유 캐시 위조 지적은 받지 않았다: 직원과 runner가 같은 OS 계정이라 MAC 키도 직원이 읽는다.
- 리뷰 반영 확인: `tests/test_annot.py` 25 passed, 영향 test 5개 파일 330 passed·3 skipped, 전체 suite 4029 passed·54 skipped. ClinVar 구간 검색과 gnomAD chrM query는 공개 API로 실측했다.
- 미해결: 속도 조절이 MCP 프로세스마다 따로라 동시 호출 합은 재시도에 맡긴다. ClinVar 위치 검색은 left-normalise된 VCF를 전제로 한다.
- 근거: `labhq/tools/annot.py`, `tests/test_annot.py`, PR #449 본문의 "API 문서" 절.
