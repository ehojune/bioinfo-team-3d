# Windows WMI 단계 진단 — lookup-only

- 실제 실패: run `39467378511`, Windows job `118782324169`, 1 failed / 10234 passed / 48 skipped. 원본 head는 `bd333485`, 실제 검사 merge는 `7c9ccbb9`(부모 main `2ce87786` + 원본 head)다. 이 merge를 작업 기준으로 통합했다.
- 확정: direct breakaway WinError 5 뒤 WMI PowerShell 10초 timeout. supervisor 이벤트·fixture 출력은 비었다. Create 결과·cold start·scheduling·provider 지연 중 원인은 미확정이다.
- 개발 중 진단은 `provider.phases`에 `powershell_started`(script 첫 marker), `input_accepted`(stdin JSON 변환 완료), `create_entered`(WMI class 준비 후 호출 직전), `create_returned`만 남긴다. 첫 marker가 없다고 OS process 생성 실패로 단정하지 않는다.
- 각 phase의 `elapsed_ms`는 PowerShell Stopwatch 시작 이후 시간이다. `provider.elapsed_ms`는 부모 monotonic clock의 전체 호출 시간으로 초기화·스케줄링·통신도 포함한다. 두 clock의 절대 시각을 비교하지 않는다.
- 최대 8KiB·5줄에서 순서가 맞는 고정 phase·유한 숫자만 채택한다. malformed/truncated 출력은 `trace_status`로 표시하며 argv·env·명령·자격 증명·stderr는 기록하지 않는다.
- timeout은 반환 marker·성공 PID JSON이 일부 잡혀도 계속 실패한다. Create는 한 번이며 반환 PID만으로 소유권·탈출을 인정하거나 종료·ACK하지 않는다. 기존 nonce/retained handle/image/owner/created/ANY-Job 증명과 ACK 전 cleanup·ACK 뒤 무 rollback kill을 유지한다.
- 로컬은 mock/static만 검증한다. 실제 Windows phase·생존·실패 경로는 총괄의 새 head CI가 필요하다. 이 진단은 원래 timeout 해결이나 성공 증거가 아니다.
- Windows 실패로 manifest가 skipped였으므로 PR #793의 새 no-reuse 경로도 아직 실제 검증되지 않았다. workflow·CI policy는 이 작업에서 수정하지 않는다.
