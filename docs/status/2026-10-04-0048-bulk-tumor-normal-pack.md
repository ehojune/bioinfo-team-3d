## 2026-10-04 · 연구 lane — 벌크 두 조건 DE pack

- 결론: `bulk_tumor_normal@1`이 pairing·발현 척도·저발현 filter·DE 기준·양성 대조를 CP1 전에 고정한다.
- 바뀐 것: 적용 조건이 맞는 pack만 계획·승인 hash·리뷰에 넣고, 양성 대조의 gene·방향·PMID 행을 검사한다. 기존 `single_cell_de@2` hash는 유지했다.
- 실행한 것: 새 test는 수정 전 collection 실패, 수정 뒤 관련 test 84 passed. README 67,501→67,452자, 연구 규약 10,533→10,549자.
- 미해결: 없음.
- 근거: `labhq/research/packs/bulk_tumor_normal.yaml`, `tests/test_research_bulk_pack.py`.
