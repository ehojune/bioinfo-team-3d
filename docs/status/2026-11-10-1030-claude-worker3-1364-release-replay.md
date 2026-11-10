# 2026-11-10 claude-worker3 — #1364: release 표시 재전송

## 결론
runner가 끊겨 있던 동안 깨우기 상한으로 해제된 단계는, 재접속 때 다시 보내는 `task.jobs_cancel`에도 `release: true`가 실린다. 그래서 runner가 이미 회수한 결과를 `jobs/late/`로 옮긴다. 요청 취소의 cancel은 그대로 표시 없이 간다.

## 바뀐 것
- `labhq/gateway/server.py`: `flush_task_cancels`가 store `jobs_cancel` 기록의 `release`를 재전송 메시지에 싣는다
- `tests/test_hpc_whole_request.py`: 재접속 재전송 시험 1건

## 실행한 것
- `tests/test_local_jobs_675.py`, `tests/test_hpc_whole_request.py`, `tests/test_gateway*.py`: 186 passed

## 미해결
- 없음
