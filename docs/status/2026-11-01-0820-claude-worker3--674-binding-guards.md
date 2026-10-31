# #674 rev14 — binding 점검 두 건의 개선

| 항목 | 내용 |
|---|---|
| 결론 | rev13이 찾은 두 개선을 넣었다. 결함 수정이 아니라 어긋남을 더 일찍·넓게 잡는다 |
| 바뀐 것 | `labhq/orchestrator/cso.py`(`prepare_recruits`의 검사에 `stored_environment`), `labhq/gateway/server.py`(`task.dispatched` publish 뒤 `_session_pin_refusal` 재확인), `tests/test_runner_env_plan_674.py` 시험 2건 |
| 실행한 것 | 새 시험 2건은 수정 전 실패, 수정 후 통과. 674 시험 세 파일과 `test_cso.py` 358건 통과 |
| 미해결 | 실제 WSL runner 요청은 하지 않았다. runner 쪽 판정은 그대로다 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/issues/674#issuecomment-6549409609 |
