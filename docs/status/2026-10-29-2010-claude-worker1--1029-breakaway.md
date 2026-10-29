# #1029 Windows breakaway 간헐 실패

- 결론: 판정이 나오지 않는 단계는 **provider_create**(WMI PowerShell provider)다. 제품 결함 증거는 없고, 원인은 CI runner의 느린 PowerShell 첫 기동 + provider의 고정 10 s 제한이다. 재현은 못 했다(Windows CI 전용 시험).
- 근거(#1028 head `7c7b7b0a` job 123396323730 events): `attempt` → `unverified`(직접 자식이 Job 안, 이 시험의 직접 경로는 항상 WMI로 넘어감) → `direct_reaped` → `wmi_attempt` → `wmi_failed provider_timeout`. provider phases: `powershell_started@0`, `stdin_open@14ms`, `header_read@4052ms`, 이후 `input_read`가 오지 않고 10 234 ms에 TimeoutExpired. 즉 stdin 헤더 읽기까지 4 s, JSON cmdlet·WMI class 로드로 남은 6 s를 넘김. 직접 경로·parent job close·생존 확인 단계는 이 실패에서 실행되지 않았다.
- 바꾼 것(`tests/test_windows_job_ci.py`만): 양성 시험 실패 메시지에 `_provider_summary`(단계·phase별 ms)를 추가했다. 예열은 넣었다가 뺐다: production은 예열 없이 10 s로 시작하므로 CI만 예열하면 첫 launch 회귀를 가린다(봇 P1).
- 미해결/열어 둠: 이슈는 열어 둔다. 원인 후보는 production의 고정 `timeout=10`(`labhq/windows_broker.py` `_create`)이 cold start 4 s+ host에서 모자란 것이다. 제한·재시도 정책은 제품 변경이라 이 범위(시험)를 넘는다. 다음 실패 때 요약 메시지가 단계를 바로 가른다.
- 미검증: 실제 Windows 실행(이 시험은 로컬에서 skip). 요약 메시지는 실패 때만 나온다.
