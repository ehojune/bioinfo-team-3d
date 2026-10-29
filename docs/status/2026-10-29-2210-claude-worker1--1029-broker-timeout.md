# #1029 broker provider 제한

- 결론: `windows_broker.py`의 고정 `timeout=10`을 `PROVIDER_TIMEOUT = TIMEOUT - BOOTSTRAP_MARGIN`(20 − 5 = 15 s)으로 바꿨다.
- 근거: 관측된 실패(#1028 head `7c7b7b0a`, job 123396323730)는 provider phases `header_read@4052ms` 뒤 `input_read` 없이 10 234 ms에 timeout이다. 제한 안에서 cold start가 4 s를 먹고 남은 6 s에 JSON 파싱·WMI create를 끝내지 못했다.
- 상한 근거: 요청은 `TIMEOUT`(20 s) 뒤 만료되고 bootstrap이 시작 때 확인한다(`windows_broker.py` `request["expires"]`). provider가 20 s까지 돌면 bootstrap이 만료된 요청을 받는다. 그래서 bootstrap 시작 여유 5 s를 남긴 15 s다.
- 한계: 15 s는 관측(≥10.2 s 필요)에 +50%를 준 값이지 측정한 상한이 아니다. 이 PR은 양성 시험이 통과 때도 `WIN1029 launch stages:`로 단계별 ms를 출력하게 해 다음 판단용 자료를 쌓는다. 한 번의 green은 해결로 보지 않는다.
- 바꾼 것: `labhq/windows_broker.py`, `tests/test_windows_broker.py`(제한이 요청 만료 안에 있음을 고정), `tests/test_windows_job_ci.py`(요약 출력).
- 미검증: 실제 Windows 실행 효과(로컬 skip), cold runner 재현.
