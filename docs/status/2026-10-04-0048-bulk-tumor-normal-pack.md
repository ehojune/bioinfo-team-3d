## 2026-10-04 · 연구 lane — 벌크 두 조건 DE pack

- 결론: `bulk_tumor_normal@1`이 pairing·발현 척도·저발현 filter·DE 기준·양성 대조를 CP1 전에 고정한다. PR #366의 봇 지적 3건도 반영했다.
- 바뀐 것: 모든 active pack은 값이나 `not_applicable` 사유를 답한다. `pairing: none`은 unpaired 모형만, raw counts는 count 모형만 허용하며 partial·complete pairing은 count 모형에도 짝 구조를 요구한다.
- 실행한 것: 수정 전 새 관련 test 10 failed, 수정 뒤 관련 test 142 passed. schema·prompt hash는 새 pack 응답 계약 때문에 갱신했다.
- 미해결: 없음.
- 근거: `labhq/research/packs.py`, `labhq/research/packs/bulk_tumor_normal.yaml`, `tests/test_research_bulk_pack.py`.
