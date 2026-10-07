## 2026-10-07 · #468 — 설치 명령 파서 fail closed

- 결론: 두 번째 봇 리뷰 P1 두 건까지 고쳐 중첩 shell 판별 부류를 구조적으로 닫았다.
- 바뀐 것: shell별 command option과 인자 소비를 구분하고, 실행 파일·subcommand·`-m` module·설치 목적지만 리터럴인지 검사한다. 일반 분석 인자의 변수 확장은 허용한다.
- 실행한 것: 환경 파서 pytest 284 passed·1 skipped, Windows 전체 pytest 4594 passed·56 skipped, public 검사 통과.
- 미해결: CI와 후속 봇 판단은 총괄이 이어받는다.
- 근거: `labhq/environment_install.py`, `tests/test_environment_install.py`, `docs/manual.md`.
