## 2026-10-06 · #435 C ② — 변이 주석 MCP labhq_annot (#449)

- 결론: 직원이 VEP·gnomAD·ClinVar를 내장 도구로 조회한다. 결과마다 DB 판본이 붙고, 작업 폴더에 조회 기록과 응답 전체가 남는다. 통제 구역에서 나온 변이도 경고·승인 없이 조회한다(PI 결정 10-06).
- 바뀐 것: `labhq/tools/annot.py`·`annot_mcp.py`, runner 배선(`auto_approve`), analyst·bioinfo-agent·biologist·lit_scout의 `builtin_mcp`, CSO roster·capability card, 연결된 도구 표·배지, `public_resources.tsv` use 문구, manual "공개 자원 조회 도구" 절. 기존 test 기대값 두 곳(bioinfo-agent `builtin_mcp`, README 배지 문구)은 이 기능이 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot.py` 20 passed(HTTP 전부 가짜), 관련 test 13개 파일 441 passed·13 skipped, 전체 suite 4024 passed·54 skipped(Windows). test 밖에서 공개 변이로 세 API를 한 번 실측해 VCV 검색어와 gnomAD 인구 요약을 고쳤다. `scripts/check_public.sh` 통과.
- 미해결: 속도 조절이 MCP 프로세스마다 따로라 동시 호출 합은 재시도에 맡긴다. gnomAD는 dataset 안의 갱신을 캐시가 구분하지 못한다.
- 근거: `labhq/tools/annot.py`, `tests/test_annot.py`, PR #449 본문의 API 문서 링크.
