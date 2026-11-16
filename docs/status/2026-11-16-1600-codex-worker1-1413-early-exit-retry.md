# #1413 — Windows 직원 조기 종료

## 결과
- `0xC000013A` 조기 종료의 프로세스 신원·Job 잔류·도구 호출·기한을 기록하고, 증거가 모두 확인되면 gateway 예산 gate에서 한 번 재시도한다.
- 스캔 누락·산출 변경·비용 또는 종료 증거 UNKNOWN은 차단한다. 최초 비용과 원래 기한을 유지하고 재시도 횟수를 저장한다.
- wrapper PID가 runner의 PID와 다르면 신원을 확인하지 못해 UNKNOWN으로 둔다. 기존 산출 검사에서 빠지는 파일이 있어도 차단한다. Codex 비용은 UNKNOWN이라 자동 재시도가 막힌다.

## 확인
- `python -m pytest tests/test_step_memory_limit.py tests/test_673_retry_budget.py tests/test_structured_output_retry.py tests/test_adapters_fake_cli.py tests/test_cso.py -q`: 343 passed, 2 skipped.
- Windows fake CLI로 정상 재시도와 비용 UNKNOWN·도구 호출·산출 변경·스캔 누락·두 번째 실패의 차단을 확인했다. 기한 소진과 종료 UNKNOWN은 별도 회귀로 확인했다.
- 공개 검사 통과. 실제 직원·모델·인스턴스·HPC 실행과 배포는 하지 않았다. 실제 종료 원인과 운영 효과는 미검증이다.
