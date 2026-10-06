## 2026-10-07 · 로그인 대기 test가 CI 부하에서 흔들리던 것

- 결론: `tests/test_login_wait.py::test_login_retry_expiry_ends_notice_and_next_failure_notifies_again`의 로그인 대기 상한을 0.2초에서 1초로 늘렸다. 검사 내용은 그대로다.
- 바뀐 것: `tests/test_login_wait.py` 한 줄과 주석.
- 실행한 것: `tests/test_login_wait.py` 45 passed. PR #457 CI의 `pytest-windows`가 이 test에서 두 번 연속 `calls == 1`로 실패했고, 같은 커밋을 다시 돌리자 통과했다.
- 미해결: 없음.
- 근거: 이 PR.
