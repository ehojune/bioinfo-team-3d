## 2026-10-07 · #456 — 공유 environment 동시 설치 격리

- 결론: 안 (a)를 택했다. 환경 단계만 공유 environment에 설치하고, 뒤 단계의 추가 패키지는 각 작업 폴더의 `./.pylib`·`./.rlib`에 격리한다.
- 바뀐 것: Claude 승인 게이트가 뒤 단계의 공유 `pip install`·`install.packages`를 거절한다. 공통 직원 지침은 격리 패키지를 단계별 `outputs/env/` 기록에 넣는다. manual의 환경 단계 절과 v0.5 로드맵도 고쳤다.
- 실행한 것: Windows 전체 pytest 4205 passed·55 skipped, 관련 pytest 254 passed·10 skipped, `scripts/check_public.sh` 통과. 실제 패키지 설치와 Codex CLI 호출은 하지 않았다.
- 미해결: Codex·Gemini·Antigravity와 셸 script 안의 간접 설치는 호출 훅이 없어 공통 지침에만 의존한다. manual의 알려진 한계에 적었다.
- 근거: `labhq/tools/approval_mcp.py`, `labhq/adapters/base.py`, `labhq/runner/daemon.py`, `tests/test_mcp_servers.py`, `docs/manual.md`.
