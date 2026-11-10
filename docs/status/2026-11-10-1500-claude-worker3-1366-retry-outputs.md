# 2026-11-10 claude-worker3 — #1366: 실패 뒤 성공한 재제출

## 결론
같은 단계에서 실패·취소된 시도 뒤에 다시 낸 잡이 성공하면, 앞선 실패는 원격 산출 판정에서 빠지고 기록에만 남는다. 성공 뒤에 실패한 시도와 아직 도는 앞 시도는 지금처럼 거절한다. #1331 5회차가 이 경로로 실패했다(7677 walltime, 7687 exit 0).

## 바뀐 것
- `labhq/runner/daemon.py` `_remote_step_outputs`: 같은 단계·revision·session의 마지막 성공보다 먼저 제출되어 끝난 비성공 시도는 건너뜀(`submitted_at` 기준)
- `tests/test_hpc_whole_request.py`: 실패→성공·취소→실패→성공 통과, 실패만·성공→실패·도는 중→성공 거절
- manual 15 한·영

## 실행한 것
- 원격 산출·회수·HPC 관련 시험: 1554 passed, 38 skipped

## 미해결
- 없음
