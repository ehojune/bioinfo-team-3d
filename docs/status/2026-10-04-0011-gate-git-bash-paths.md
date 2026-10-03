## 2026-10-04 · 게이트 — Git Bash 경로

- 결론: 12차 모의 시운전(일반 lane, v0.25 사전 점검)에서 data_steward가 자기 작업 폴더 `.tmp`에 쓸 때마다 게이트가 PI에게 물었다(같은 단계 3장). Claude의 Bash는 Windows에서 Git Bash라 경로를 `/c/Users/...`로 쓰는데, 허용 루트는 `C:/Users/...`여서 비교가 안 됐다.
- 바뀐 것: `labhq/policy.py` `_evaluate_tool` — Bash이고 루트가 Windows 드라이브 경로면 셸 쓰기 대상을 `_git_bash_path`로 바꿔 비교한다(Claude 쓰기 도구가 이미 쓰는 변환). 바꿀 수 없는 경로는 그대로 둬 묻는다. POSIX 러너의 `/c/...`는 POSIX 폴더 그대로다.
- 실행한 것: 관련 test 478 passed. 새 test 2건은 수정 전 실패.
- 미해결: 없음.
- 근거: `tests/test_shell_write_quotes.py::test_git_bash_drive_paths_are_read_as_windows_paths`.
