## 2026-10-06 · #35 시설팀 2단계 — environment 실패 종류와 오류 서명 표 (PR #447)

- 결론: 환경 고장(패키지·명령·R 패키지 없음, 디스크 부족, 이름 풀이·프록시, Docker 데몬, Codex sandbox 준비)이 `environment`로 분류되고 재시도하지 않는다. 웹 작업판·보고서·`labhq status`에 "환경 문제: 원인 — 할 일"이 뜬다.
- 바뀐 것: `labhq/facilities/signatures.yaml`·`.py`, `failure_kind`(login·quota 다음), runner의 실패 출력 수집(stderr 끝·실패한 도구 호출만), `TaskResult.environment`, `request.step_done`, 보고서·라운드 기록·request summary·snapshot, `labhq status`, 웹 카드, manual 절.
- 실행한 것: 전체 `pytest -q` 4068 passed·54 skipped(Windows), 바꾼 곳 test 367 passed·1 skipped(node 포함), 새 test 63개, `scripts/check_public.sh`.
- 미해결: 시설팀 직원(Codex read-only)·`facilities_fix` allowlist·자동 재개는 3단계.
- 근거: #35 PI 결정(10-06, A 내부 구조 먼저), `tests/fixtures/environment_signatures.yaml`.
