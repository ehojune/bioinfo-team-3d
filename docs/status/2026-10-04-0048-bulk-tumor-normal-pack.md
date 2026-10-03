## 2026-10-04 · 연구 lane — 벌크 두 조건 DE pack

- 결론: `bulk_tumor_normal@1`의 core 통계 계약과 scale·pairing·model 조합을 기계 판정한다.
- 바뀐 것: 분석 단위·다중검정·민감도 분석은 중복 pack 필드를 없애고 core 값을 정본으로 삼았다. 모형 규칙은 허용 조합표 하나로 닫았다.
- 실행한 것: 두 번째 봇 지적용 test는 수정 전 10 failed, 수정 뒤 관련 test 151 passed. 기존 rule id와 pack hash test는 새 단일 표와 pack 내용 변경 때문에 갱신했다.
- 미해결: 없음.
- 근거: `labhq/research/contract.py`, `labhq/research/packs.py`, `labhq/research/packs/bulk_tumor_normal.yaml`, `tests/test_research_bulk_pack.py`.
