# #1144 로컬 job 폴링이 취소로 끝난 job을 덮어쓰는 경합

- 결론: `_poll_local_jobs`가 `await to_thread(local_jobs.status)`로 상태를 읽는 사이에 취소 경로(`_end_local_job`)가 job을 `cancelled`·`terminal`로 끝내면, 폴링이 깨어나 읽어 둔 상태(예: 방금 정상 종료한 `completed`)로 job을 덮고 `job.state`를 한 번 더 보냈다. 그러면 취소된 단계가 깨어날 수 있었다. 창은 job이 자연히 끝나는 순간과 취소가 겹칠 때뿐이라 좁다.
- 바꾼 것: `labhq/runner/daemon.py`의 `_poll_local_jobs`가 상태를 읽은 뒤 `if j["terminal"]: continue`로 낡은 읽기를 버린다(한 줄). 취소의 결과가 마지막이다.
- 시험(`tests/test_local_jobs_675.py` 1건): 상태 읽기를 이벤트로 막아 둔 채 `task.jobs_cancel`로 job을 끝내고 읽기를 풀면, job은 `cancelled`로 남고 `job.state`는 `cancelled` 하나뿐이며 `jobs.finished`가 나가지 않는다. main의 코드에서는 `completed`로 덮여 실패한다. 파일 전체 37 passed, 1 skipped.
- 미확인: HPC job 폴링(`_poll_jobs`)에 같은 패턴이 있는지는 이번 범위가 아니라 보지 않았다(HPC job은 이 취소 경로를 쓰지 않는다). 창이 좁아 실제 발생 빈도는 재지 못했다.
- 근거: #1139 판정 댓글 6535399863, #1144.
