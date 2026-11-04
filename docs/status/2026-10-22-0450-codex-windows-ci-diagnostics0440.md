## #823 — Windows pool 실패 진단과 시설팀 fallback 검사의 대기 단언 (source c873b85, base 86a5c05)

- `scripts/ci_suite.py`의 `StageDiagnostics`가 process별 `diagnostics-<worker>.json`을 쓴다.
  - 진단 전용이며 재사용하지 않는다(`diagnostic_only=true`, `reusable=false`).
  - 실패 stage마다 duration·예외 kind·crash 첫 줄을 즉시 원자적으로 기록한다. 첫 줄은 300자로 자르고 제어문자를 지우며 token을 가린다.
  - 느린 stage 상위 30개를 남긴다. 실패는 50건, nodeid는 512자, duration은 유한한 0~86400초까지 받는다. 중복과 쓰기 실패는 test 결과를 바꾸지 않는다.
- proof·metrics 형식, receipt, 재사용 판정은 바꾸지 않았다. phase 검증은 `collection-`·`metrics-`·`*error*` 이름만 읽으므로 새 파일은 그 판정에 들어가지 않는다.
- `tests/test_consult_restart.py::test_facilities_ask_falls_back_to_the_cso_when_facilities_never_returns`:
  - 실제 `wait_agent_online`을 감싸 `("facilities", 0.2)`가 offline(`False`)으로 끝났고 0.2초 동안 기다렸는지 확인한다. 하한은 clock resolution을 고려했다.
  - CSO fallback, `routed_to`, `state=resolved`, answer의 `from`도 확인한다.
  - `wait_for`는 기존 case처럼 60초 hang 감시로만 둔다. 다른 2~3초 case는 바꾸지 않았다.
  - offline 종료는 이 case가 실제 함수로 확인하고, 등록 신호와 timeout 0은 `tests/test_cso.py:893-906`이 확인한다.
- 이 case는 Windows 선택에 그대로 남는다(cohort). production 대기, MCP 20분, CI job 10분·step 7분, 필수 26, native 실패, 선택 범위는 바꾸지 않았다.
- 로컬 검사(guard 유지, blocked 0):
  - `tests/test_ci_windows_phases.py` 104 passed. 새 순수 진단 검사가 포함된다.
  - CI 정책 검사 4파일 381 passed.
  - `validate_profile`, `include_windows`, Python 3.10 AST, `check_public.sh`, `git diff --check` 통과.
- 미실행: consult case(Hub async)는 CI에 맡긴다. #837 실패가 TimeoutError였는지는 미확인이며, 다음 Windows run의 진단 파일로 확인한다.
