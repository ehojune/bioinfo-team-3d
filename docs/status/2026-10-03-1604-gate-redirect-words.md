## 2026-10-03 · 게이트 오탐 — 리다이렉트 대상을 명령 목적지로 읽음

- 결론: `cp a outputs/ 2>/dev/null`의 /dev/null을 cp 목적지로 읽던 오탐을 고쳤다(7차 모의 시운전). PowerShell `Copy-Item ... 2>$null`도 같다.
- 바뀐 것: `labhq/policy.py` `_shell_write_targets`가 명령 이름 기반 검사 전에 리다이렉트 구간을 지운다. 리다이렉트 대상은 기존 리다이렉트 검사가 그대로 판정한다.
- 실행한 것: 관련 test 451건 통과, 새 test 6건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py`.
