# #1076 시험의 전역 시계 patch를 모듈 proxy로

- 결론: 시험 16개 파일이 `X.time.f`로 process 전체의 `time` 함수를 바꾸던 곳을 모두 모듈 자신의 `time`(또는 `asyncio`) 이름만 바꾸는 proxy로 고쳤다.
- 바꾼 것: `tests/support/time_proxy.py`에 `RuntimeProxy`를 두고 16개 파일이 여기서 import한다. 단언과 시험 의도는 그대로다. `test_doctor`의 roster 시험은 시계를 읽는 `labhq.doctor`와 `labhq.registry` 둘을, `test_login_wait`는 `labhq.orchestrator.cso`를 바꾼다. `test_local_jobs_675`의 `_wait_for_pid`는 시험 파일 안에 있어 그 파일의 `time` 이름을 바꾼다. 범위 밖: `test_ci_windows_phases.py`(자체 정의 유지), 다른 파일의 `asyncio.create_subprocess_exec` 등 시계가 아닌 patch.
- 실행한 것: 바꾼 16개 파일의 시험을 로컬(Windows)에서 돌렸다. 실패는 main에서도 같은 2건뿐이다: `test_doctor::test_doctor_warns_about_parent_session_markers_and_pi_skills`, `test_staff_control_steps::test_a_cli_start_the_runner_cannot_record_ends_the_cli`.
- 미확인: 전체 suite는 CI에 맡겼다. 전역 patch가 Windows CI flake의 원인이었는지는 이번에 증명하지 않았다.
- 근거: #1076, `tests/support/time_proxy.py`.
