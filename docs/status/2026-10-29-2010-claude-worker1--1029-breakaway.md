# #1029 Windows breakaway 간헐 실패

- 결론: 판정이 나오지 않는 단계는 **provider_create**(WMI PowerShell provider)다. 제품 결함 증거는 없고, 원인은 CI runner의 느린 PowerShell 첫 기동 + provider의 고정 10 s 제한이다. 재현은 못 했다(Windows CI 전용 시험).
- 근거(#1028 head `7c7b7b0a` job 123396323730 events): `attempt` → `unverified`(직접 자식이 Job 안, 이 시험의 직접 경로는 항상 WMI로 넘어감) → `direct_reaped` → `wmi_attempt` → `wmi_failed provider_timeout`. provider phases: `powershell_started@0`, `stdin_open@14ms`, `header_read@4052ms`, 이후 `input_read`가 오지 않고 10 234 ms에 TimeoutExpired. 즉 stdin 헤더 읽기까지 4 s, JSON cmdlet·WMI class 로드로 남은 6 s를 넘김. 직접 경로·parent job close·생존 확인 단계는 이 실패에서 실행되지 않았다.
- 바꾼 것(`tests/test_windows_job_ci.py`만): provider와 같은 cmdlet·WMI class를 한 번 미리 로드하는 `_warm_wmi_provider`(경과 시간을 stderr에 `WIN1029`로 출력), 실패 메시지에 `_provider_summary`(단계·phase별 ms) 추가.
- 미해결/열어 둠: 한 번의 green은 해결이 아니다. 예열 뒤에도 `provider_timeout`이 나오면 제품 쪽 제한(`labhq/windows_broker.py` `_create`의 `timeout=10`)이 느린 host에서 여유가 부족하다는 근거이며 별도 issue 소관이다. 이 PR은 `Refs #1029`로 이슈를 닫지 않는다.
- 미검증: 실제 Windows 실행(이 시험은 로컬에서 skip). 예열이 효과 있는지는 CI의 `WIN1029 wmi warm-up` 줄로 확인해야 한다.
