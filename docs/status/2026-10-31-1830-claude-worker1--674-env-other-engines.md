# #674 Codex·Gemini 소비 단계에도 검증된 환경 전달

- 결론: 검증을 통과한 환경(`validate_runner_env`)을 `ENVIRONMENT_ENGINES = {claude_code, codex, gemini}` 소비 단계가 받는다. 세 엔진 모두 env `LABHQ_ENV_PYTHON`(검증된 interpreter 절대 경로)·`PYTHONDONTWRITEBYTECODE=1`과 브리핑 `[Runner environment]` 단락을 받고, 읽기 전용 root(`read_dirs`)는 기존대로 Claude만 더 연다(`ENVIRONMENT_GRANT_ENGINES`). antigravity·cli·mock·알 수 없는 agent는 기존 거부를 유지한다.
- 근거: [#1178 결과](https://github.com/ehojune/bioinfo-team-3d/issues/1178#issuecomment-6540356076) — Windows `codex sandbox`는 작업 폴더 밖 읽기를 허용하고 쓰기만 막는다(grant 불필요). Gemini는 읽기 전용 grant를 표현할 수 없다. [총괄 결정](https://github.com/ehojune/bioinfo-team-3d/issues/674#issuecomment-6540361713).
- 그대로인 것: `.venv` 아래 통제 구역 링크 검사·목록 미완료 거부·UNC(쓰기 거부 규칙을 못 만드는 경로) 거부·읽기 전용 소비 단계 거부·검증 실패 시 grant 제거는 세 엔진에 똑같이 적용된다.
- 바꾼 것: `labhq/runner/daemon.py`, `tests/test_environment_grant_674.py`(+4건: Codex·Gemini가 경로만 받음, 두 엔진에도 fail-closed 검사 적용, cli는 거부). `step_inputs.py`는 바꾸지 않았다.
- 시험: 신규 4건 포함 환경·입력·e2e 관련 101건 통과.
- 미검증: WSL·Linux의 Codex sandbox가 작업 폴더 밖 읽기를 허용하는지(#1178은 Windows만 실측), Codex·Gemini가 실제로 그 interpreter를 실행하는지, manual 한·영(차례를 기다림).
