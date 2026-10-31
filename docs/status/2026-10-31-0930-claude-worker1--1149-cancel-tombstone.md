# #1149 task보다 먼저 도착한 task.cancel이 사라지는 문제

- 결론: runner가 보유도 접수도 하지 않은 task의 `task.cancel`을 받으면 `cancelled_task` 행(tombstone)을 영속 저장한다. 같은 id의 `task.dispatch`가 접수 기록 없이 오면 시작하지 않고 `task.accepted` + 취소 결과(`ok=False`, `error_kind="cancelled"`, `cleanup_evidence.status="NOT_STARTED"`)를 한 묶음으로 보내고 `accepted_task`를 `finished`로 남긴 뒤 tombstone을 지운다. 이후 같은 dispatch가 다시 와도 접수 기록이 있어 시작하지 않는다.
- 원인(#1147 판정 ①): 취소는 `self.tasks`에 task가 없으면 `_jobs_cancelled`에만 id를 넣고, 이 집합은 job 제출만 막는다. 재연결 때 `flush_task_cancels`가 `task.dispatch` 재전송보다 먼저 도착하면 취소가 사라진다.
- 시험(`tests/test_runner_cancel_tombstone.py`, 5건): 취소→dispatch는 시작 안 함(수정 전 `_run_guarded` 호출로 실패), 재전송 dispatch도 시작 안 함(수정 전 실패), dispatch→취소는 tombstone 없음, 다른 task 취소는 영향 없음, 2일 지난 tombstone은 다음 취소 때 정리(수정 전 실패).
- 바꾼 것: `labhq/runner/daemon.py`, `tests/test_runner_cancel_tombstone.py`, `config/ci/windows-boundary-v1.json`, `docs/manual.md`·`manual.en.md` 각 한 줄.
- tombstone 정리: 처리 시 삭제, 아니면 2일(`CANCEL_TOMBSTONE_KEEP_S`, gateway의 `TASK_CANCEL_KEEP_S`와 같음) 지난 것을 다음 취소 때 삭제.
- 미검증: 실제 ws 재연결 순서, Windows 실행. 범위 밖: ② pause/resume 순서, ③ 더블 클릭 중복.
