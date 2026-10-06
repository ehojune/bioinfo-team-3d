## 2026-10-07 · #456 — 공유 environment 동시 설치 격리

- 결론: 환경 단계만 공유 environment를 바꾸고, 뒤 단계의 추가 패키지는 `./.pylib`·`./.rlib`에 격리한다.
- 바뀐 것: 보호 요청의 설치 가능 shell 규칙을 승인 게이트로 보내고, 경로가 붙은 Python package manager와 R 설치 호출별 library를 검사한다.
- 실행한 것: Windows 전체 pytest 4214 passed·55 skipped, 관련 pytest 457 passed·11 skipped, public·패치노트·목차 검사 통과.
- 미해결: Codex·Gemini·Antigravity와 셸 script 안의 간접 설치는 호출 훅이 없어 공통 지침에 의존한다.
- 근거: `labhq/environment_install.py`, `labhq/tools/approval_mcp.py`, `labhq/adapters/claude_code.py`, `tests/test_mcp_servers.py`, `tests/test_private_paths.py`.
