## 단계
#1076: WMI `provider_timeout` 재발 3건의 분석과 진단 추가(동작 불변).

## 한 일
- `docs/reference/ci-1076-wmi-provider-timeout.md`: 세 건(A `42151602313`, B `42221743480` attempt 1, C `42224884369`)의 증거, 성공 run 5개의 기준선(첫 launch `header_read` 1.0–2.2 s, 둘째 72–107 ms, 이 PC 약 85 ms), 원인 후보 H1–H4, 고칠 범위, 추가한 진단을 적었다. 세 건 모두 `create_entered`에 닿지 않았고 그 job의 첫 launch였다.
- `labhq/windows_broker.py::_create`: `diagnostics`에 `run_ms`(호출 시간, `perf_counter`), `launch_ordinal`, `os_uptime_s`를 더했다. `subprocess.run` 호출 형태, `time.monotonic` 읽기 횟수, timeout·재시도 동작은 그대로다.
- `tests/test_windows_broker_diag_1076.py`: 성공·timeout·호출 형태 불변·journal 행.

## 확인
- Windows: 새 시험과 기존 broker 시험(`test_windows_broker*.py`, `test_clean_install_diagnostics.py`, `test_windows_job_diagnostics.py`, `test_web_shutdown_process.py`) 208 passed. 기존 시험은 고치지 않았다.
- 처음에는 `time.monotonic`을 한 번 더 읽어 기존 시험 22건이 `StopIteration`으로 깨졌다(시험이 그 시계의 읽기 횟수를 센다). `perf_counter`로 바꿔 기존 시험을 그대로 통과시켰다.

## 하지 않은 것
- provider CPU 시간과 kill 시각: `subprocess.run`을 `Popen`으로 바꿔야 해서 뺐다(총괄 결정).
- CI 워밍업과 재시도, `PROVIDER_TIMEOUT` 변경.
- clean-install·`test_web_shutdown_process.py`의 journal 투영에 새 필드를 넣는 일(claim 밖).
