## 단계
#912 rev2: issue 정리 A 문서를 올린다. 총괄이 더한 개선 한 건(`cso.py`의 `stored_environment`)은 확인 결과 이미 main에 있어 코드는 바꾸지 않았다.

## 한 일
- `docs/reference/issue-hygiene-20261107-a.md`: #912·#560·#618·#1070을 닫은 근거, #963·#674를 열어 둔 이유와 재개 조건.
- #674 절 정정: rev13이 올린 작은 개선 두 건은 PR #1254(`b2d7d807`, 11-01)로 이미 main에 있다. `cso.py:7451`이 `stored_environment`를 넘기고, `server.py:4431-4433`이 기록·전송 직전에 `_session_pin_refusal`을 다시 부른다. #674에도 정정 댓글을 남겼다.

## 확인
- `tests/test_runner_env_plan_674.py` 25 passed(Windows 로컬). 해당 시험: `test_a_resumed_bound_plan_is_checked_with_its_scheduler_made_environment_steps`, `test_a_runner_that_changes_runtime_while_task_dispatched_is_published_is_not_sent_the_stale_binding`.

## 하지 않은 것
- 실패하는 시험을 먼저 넣는 일은 하지 않았다. 고칠 결함이 이미 고쳐져 있었다.
- 코드는 바꾸지 않았다.
