## 2026-10-08 · 경고와 카드 소음 축소 (PI 점검 R20)

- 결론: `outputs/scripts/`·`outputs/reference/`·`outputs/env/`의 미선언 파일을 `unreported_outputs`와 요청 묶음에 보존하고, 사람이 보는 경고에서만 숨겼다.
- 바뀐 것: runner 로그·CP2 카드·`labhq verify` 경고가 공통 필터를 쓴다. `__pycache__`는 전처럼 목록에서도 제외한다.
- 실행한 것: 수정 전 회귀 1 failed, 수정 뒤 관련 pytest 191 passed·5 skipped. Windows 전체 pytest 4826 passed·60 skipped, `scripts/check_public.sh`·패치노트·목차 검사와 `git diff --check` 통과.
- 미해결: push 뒤 CI 확인.
- 근거: `labhq/artifact_policy.py`, `labhq/runner/daemon.py`, `labhq/evidence/audit.py`, `labhq/orchestrator/cso.py`, `tests/test_observed_outputs.py`, `tests/test_labhq_verify.py`, `tests/test_research_cp2.py`, `docs/manual.md`.
