## 2026-10-03 · PR 준비 — 연구 결과 계약 위반 행 분리(#90, #298)

- 결론: 교정 뒤에도 남은 행 단위 위반은 해당 행·파생 행·link만 빼고 CP2로 넘긴다. 구조 위반은 계속 단계를 실패시킨다.
- 바뀐 것: 결과 교정 기본값을 2회로 올리고, 거부 행과 unsupported claim을 CP2 카드·receipt·최종 보고서·감사 번들에 남겼다.
- 실행한 것: 새 회귀는 수정 전 실패했다. 관련 pytest 378건·Node 4건, schema·prompt hash, 공개 저장소·diff 검사가 통과했다.
- 미해결: 없음.
- 근거: `labhq/research/contract.py`, `labhq/orchestrator/cso.py`, `labhq/evidence/audit.py`, `labhq/web/ui/decide.js`, `tests/test_research_cp2.py`, `tests/test_research_report.py`.
