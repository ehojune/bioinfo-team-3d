## 2026-10-07 · #35 시설팀 3단계 — 승인된 환경 수정 (PR #457)

- 결론: 봇 P1 4건을 고쳐 패키지는 원 단계의 격리 폴더에 설치하고, 복원 승인과 `disk_full` 정리가 멈추지 않게 했습니다.
- 바뀐 것: 직원 산출물 interpreter 실행 제거, 고정 `./.pylib`·`./.rlib` 재실행 지시, 승인 거절·만료 복구, 캐시 우선 정리, manual.
- 실행한 것: 관련 272 passed·1 skipped, 재현 39 passed, Windows 전체 pytest 4260 passed·55 skipped, Node CJS 28개, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `labhq/facilities/fixes.py`, `labhq/facilities/fixes.yaml`, `tests/test_facilities_fixes.py`.
