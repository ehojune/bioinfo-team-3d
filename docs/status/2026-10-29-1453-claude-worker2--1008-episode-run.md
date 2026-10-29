# 해제 episode의 실행별 ID (#1008)

| 항목 | 내용 |
|---|---|
| 문제 | runner 메모리의 episode 카운터는 재시작·task 재전송 때 0부터 다시 센다. 이전 실행의 해제 명령(episode N)이 새 실행의 N번째 제한에 맞으면 잘못 풀린다 |
| 방식 | 저장하지 않고 ID를 바꾼다. `Escalation.run`(실행마다 새 값) + 카운터 = `episode_id`(문자열). 이전 실행의 명령은 어떤 새 제한과도 맞지 않아 `stale` |
| 바뀐 곳 | `runner/breaker.py`, `runner/daemon.py`, `gateway/server.py`(episode를 문자열로), manual 한·영 |
| 시험 | `tests/test_breaker_enforcement.py` 갱신·추가 |
| 미확인 | 실제 runner 재시작·network E2E |
