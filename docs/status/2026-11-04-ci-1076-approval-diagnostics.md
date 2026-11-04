# #1076 approval 시험 진단

- 송신 취소·timer·task stack·runner/lock·publish 상태를 실패 시 기록한다. publish barrier로 송신 timeout 이후 대기를 따로 검증한다.
- Windows 로컬 Python 3.12: `tests/test_approval_durable_606.py` 45 passed, CI 선택 정책 시험 129 passed.
- 이전 Windows 실패 원인은 미확인이다. #1076 종료는 보류하고 web cleanup은 별도 후속으로 남긴다.
