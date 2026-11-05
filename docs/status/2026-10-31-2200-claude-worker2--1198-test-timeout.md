# #1198: 시험마다 300초 상한으로 CI 정지를 한 시험의 실패로 바꾼다

- 결론: `pytest-timeout==2.4.0`과 `timeout = 300`을 넣었다. Linux 3.10/3.12는 `timeout_method=signal`(멈춘 시험만 실패, 나머지 계속), Windows는 `thread`(worker 덤프 후 종료, xdist가 그 시험만 crash 보고). job `timeout-minutes`는 그대로다.
- 원인 분석(#1198 댓글): PR #1192 3.10 job 덤프에서 두 worker 모두 main thread가 async 시험의 `run_until_complete → select()`에서 이벤트를 기다렸고 남은 thread·subprocess는 없었다. 진단은 controller의 "마지막 보고 시험"만 보여 멈춘 시험을 가리키지 못했다. 어느 시험인지는 로그만으로 특정하지 못했다.
- 바뀐 것: `pyproject.toml`, `.github/workflows/test.yml`(Linux 둘 signal, Windows 둘 thread), `scripts/ci_suite.py`(상한 5초 전에 시험 이름과 전 thread 스택을 stderr에 출력, 프로토콜 wrapper를 `tryfirst`로 두어 host-resource lock 대기는 상한에 안 든다), `docs/reference/ci-reuse.md`, `config/ci/windows-boundary-v1.json`(새 시험 inventory), `tests/test_ci_suite.py`(신규).
- 실행한 것: `test_ci_suite`·`test_ci_parallel_policy`·`test_ci_windows10m`·`test_ci_windows_phases`·`test_ci_normative_prose`·`test_ci_boundary_preview`·`test_ci_manifest_identity` 570 passed, 1 skipped(Windows에서 signal 시험).
- 미해결/미검증: Linux signal 경로(신규 시험의 실제 실행은 Linux CI에서 처음), thread 방식의 xdist crash 보고, 멈춘 시험의 원인 자체는 상한이 이름을 알려준 뒤 별도로 고친다. 느린 시험은 `@pytest.mark.timeout(n)`으로 연장한다.
