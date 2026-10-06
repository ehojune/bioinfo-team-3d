## 2026-10-07 · #35 시설팀 3단계 — 승인된 환경 수정 (PR #457)

- 결론: environment 실패 세 종류를 PI 승인 뒤 allowlist로 고치고, 같은 작업 폴더에서 원 단계를 한 번 다시 돌립니다.
- 바뀐 것: Python·R 패키지 이름 검증, 작업 폴더 캐시 정리, `facilities_fix` 승인·재시작 복구·실행 기록, 웹 카드·피드, manual.
- 실행한 것: 관련 Python 468 passed·1 skipped, Windows 전체 pytest 4236 passed·55 skipped, Node CJS 27개, `scripts/check_public.sh`.
- 미해결: 없음.
- 근거: `labhq/facilities/fixes.py`, `labhq/facilities/fixes.yaml`, `tests/test_facilities_fixes.py`.
