## 2026-10-07 · #468 — 설치 명령 파서 fail closed

- 결론: 판별할 수 없는 설치 선택자와 중첩 shell을 거절하고, command group의 cwd를 밖으로 누출하지 않는다.
- 바뀐 것: 비리터럴 executable·subcommand·`-m` 모듈 판정, subshell·brace cwd 복원, shell 본문 3단계 재귀 검사를 추가했다.
- 실행한 것: 관련 pytest 251 passed·11 skipped, Windows 전체 pytest 4532 passed·56 skipped, public 검사 통과.
- 미해결: CI와 자동 봇 리뷰는 PR #468에서 확인한다.
- 근거: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
