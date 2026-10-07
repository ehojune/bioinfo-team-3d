## 2026-10-07 · #468 — 설치 명령 파서 fail closed

- 결론: 첫 봇 리뷰 P1 세 건을 고쳐 brace cwd 누출과 동적 중첩 shell 우회를 막고 R·Python·awk·sed 분석 명령 오탐을 없앴다.
- 바뀐 것: 따옴표 출처를 토큰에 보존하고, `()`만 cwd를 복원하며, 동적 shell 본문은 뒤 인자까지 검사한다. owner·consumer 회귀를 함께 추가했다.
- 실행한 것: 환경 파서 pytest 248 passed·1 skipped, Windows 전체 pytest 4558 passed·56 skipped, public 검사 통과.
- 미해결: CI와 후속 봇 판단은 총괄이 이어받는다.
- 근거: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
