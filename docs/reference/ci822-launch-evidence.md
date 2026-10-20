# PR #822 Windows 시작 실패 — lookup-only

- 완료된 Windows job `118629704886` / run `39423359982`: 1 failed, 9567 passed, 48 skipped. 실패는 `test_the_supervisor_logs_the_exit_code_of_a_role_that_fails`의 supervisor 시작 전이다.
- 확정: `_create()`의 PowerShell `subprocess.run(timeout=10)`이 `TimeoutExpired`를 냈고 broker가 `native_or_protocol`로 실패했다. exit-code assertion에는 도달하지 않았다.
- 미확정: WMI Create 성공 여부, provider·호스트 지연·프로토콜 정체 중 무엇이 원인인지. #774 AST 수정과의 연결 근거도 없다. infrastructure flake라고 단정하지 않는다.
- fixture 시작 예외에도 기존 launch journal·fixture 로그의 끝 8192자를 붙인다. 원래 예외 체인을 보존하며 진단 파일 읽기 실패도 원인을 대체하지 않는다.
- 제품 timeout·launch·identity·ANY-Job·ACK·cleanup은 그대로다. unknown Create는 재시도하지 않는다. 실제 Windows integration assertion도 유지한다.
- mock 회귀 6개와 Python 3.10 AST 통과. 실제 WMI·프로세스 검증은 로컬 미실행이며 원래 Windows 실패는 미해결이다.
- 후속 Linux fake 계약 수정: 진단 wrapper의 AssertionError와 원래 LaunchError의 cause 동일성을 검사한다. 호출 1회·stream close·무 PID 종료·무 ACK를 유지하며 관련 mock 14개가 통과했다. 제품·Windows integration은 변경하지 않았다.
- 총괄 권고: 이 진단 head의 승인된 Windows CI에서 증거를 수집한다. 코드 변경 없이 재현성만 확인하려면 실패한 원래 head의 Windows job 1회 재시도도 가능하나 unknown Create의 성공·정리를 입증하지는 않는다. 이 작업에서는 재시도·CI 조회를 하지 않는다.
