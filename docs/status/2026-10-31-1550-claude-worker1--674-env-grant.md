# #674 검증된 runner 환경의 읽기 grant(B안, Claude 한정)

- 결론: 환경 단계가 만든 `.venv`가 `validate_runner_env` 검증(runner 기록·manifest hash·pinned binding·interpreter 링크 대상)을 통과하면, 소비 단계의 agent가 Claude일 때 `_environment_refusal_reason`이 거부하지 않고 검증된 (venv, python) 경로를 `self._env_grants[task.id]`에 남긴다. `_run_task`가 이를 `_apply_environment_grants`로 `read_dirs`(읽기 전용, Claude가 Edit/Write 거부 규칙을 붙임)에 더하고 env `LABHQ_ENV_PYTHON`·`PYTHONDONTWRITEBYTECODE=1`, 브리핑 `[Runner environment]` 한 단락을 더한다. 검증이 한 번이라도 실패하면 이전 grant도 지운다. 읽기 전용 task는 grant를 받지 않는다.
- 그 밖의 엔진(Codex·Gemini·cli·알 수 없는 agent)은 `read_dirs`를 받지 않아 기존 "protected interpreter alias is not implemented" 거부를 유지한다. `ENVIRONMENT_ALIAS_AVAILABLE`은 False 그대로다(미래의 별칭이 모든 엔진을 여는 용도). 새 상수 `ENVIRONMENT_GRANT_ENGINES = {claude_code}`.
- 결정: [#674 결정 메모](https://github.com/ehojune/bioinfo-team-3d/issues/674#issuecomment-6539968861)와 총괄 결정(B안, Claude 한정).
- 바꾼 것: `labhq/runner/daemon.py`, `tests/test_environment_grant_674.py`(7건), `config/ci/windows-boundary-v1.json`. `step_inputs.py`는 `validate_runner_env`가 이미 venv 경로를 돌려줘 바꾸지 않았다.
- 시험: 신규 7건(Claude 허용, 타 엔진 거부, 검증 실패 시 grant 제거, binding 없음, read_dirs·env·브리핑, 읽기 전용 제외, 플래그 동작)은 구현 전 4건이 실패했고 지금 통과. 환경·입력·예약 기록 관련 기존 시험 포함 314건 통과.
- 미검증: 실제 Windows·WSL에서 Claude가 grant된 venv의 interpreter를 실행하는지와 승인 카드가 뜨는지(mock만), 실제 ② 시운전, Codex 소비 단계, manual 한·영(차례를 기다림).
