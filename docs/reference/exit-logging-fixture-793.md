# Exit logging 전용 fixture — lookup-only

- 기준 `fc70f4a6`: run `39496854379`, Windows job `118884124968`는 logging test만 실패했다(10406 passed / 48 skipped). `powershell_started` 88.1948ms만 수신했고 부모 경과 시간은 16203ms였다. supervisor 이벤트·fixture 출력은 비었다.
- `ReadToEnd() | ConvertFrom-Json`은 첫 marker와 `input_accepted` 사이에 있다. 따라서 EOF/read·JSON·관측·스케줄링 구간을 구분할 수 없으며 Create가 실행되지 않았다고도 단정하지 않는다. payload·env·명령은 추가 수집하지 않았다.
- [Python 3.12 문서](https://docs.python.org/3.12/library/subprocess.html#subprocess.run)는 `input`을 stdin pipe로 전달하며 timeout 뒤 자식 종료를 기다린다고 설명한다. [Microsoft ReadToEnd 문서](https://learn.microsoft.com/en-us/dotnet/api/system.io.streamreader.readtoend)는 EOF에 도달하지 않는 protocol에서 blocking 가능성을 설명한다. 어느 설명도 이번 원인을 입증하지 않는다.
- CPython 3.12 소스의 Windows 경로는 stdin write/close 뒤 reader thread를 기다리고, run의 timeout 처리 후 출력도 회수한다. `input`·명시적 flush가 없는 버그로 바로 바꿀 근거는 없다. EOF ownership·JSON 지연·출력 관측의 실제 원인은 UNVERIFIED다.
- logging test 한 건만 일반 Popen과 정확한 fake gateway 명령 전용 launcher를 사용한다. launcher 대체는 그 자식 fixture 안에만 존재하며 production launch 증거로 세지 않는다. 기존 lifecycle/PID/exit-code assertions 8개와 teardown은 그대로다. managed-entry guard가 이미 요청됐으면 지우지 않고 fixture 시작을 거부한다.

| 기존 실제 Windows CI gate (파일 `tests/test_windows_job_ci.py`, 변경 없음) | 남는 증명 |
|---|---|
| `test_allowed_breakaway_child_survives_parent_job_close` | 성공 필수·ANY-Job 비소속·부모 Job 닫은 뒤 소유 child 생존 |
| `test_wmi_escapes_owned_outer_nonbreakaway_inner_silent_job` | WMI 한 번·소유 direct 정리 순서·supervisor/role Job 비소속·양쪽 Job 닫은 뒤 생존 |
| `test_denied_direct_and_mock_denied_provider_record_no_pid_or_readiness` | 거부 시 PID/readiness 없음 |
| `test_real_wmi_bootstrap_never_enters_fake_role_without_verified_ack` | unknown Job/receipt timeout 시 ACK 전 role/ready/PID 없음·소유 handle cleanup |

- 다른 real supervisor tests는 기존 detached fixture를 계속 사용한다. 제품·timeout·단일 Create·identity/nonce/retained handle/ANY-Job/ACK/cleanup·CI selector는 변경하지 않았다. 원래 provider timeout은 미해결이며 이 변경은 그 수리가 아니다.
- 로컬 검증은 fake/string/AST뿐이다. ordinary logging fixture와 위 실제 Windows gates는 root 검토 뒤 exact-head CI가 필요하다. push·CI 재시도·병합·배포는 하지 않는다.
