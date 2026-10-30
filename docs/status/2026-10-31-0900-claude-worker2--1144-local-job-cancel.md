# #1144 로컬 job 폴링이 취소로 끝난 job을 덮어쓰는 경합

- 결론: `_poll_local_jobs`가 `await to_thread(local_jobs.status)`로 상태를 읽는 사이에 취소 경로(`_end_local_job`)가 job을 `cancelled`·`terminal`로 끝내면, 폴링이 깨어나 읽어 둔 상태(예: 방금 정상 종료한 `completed`)로 job을 덮고 `job.state`를 한 번 더 보냈다. 그러면 취소된 단계가 깨어날 수 있었다. 창은 job이 자연히 끝나는 순간과 취소가 겹칠 때뿐이라 좁다.
- 바꾼 것: `labhq/runner/daemon.py`의 `_poll_local_jobs`가 상태를 읽은 뒤 `if j["terminal"]: continue`로 낡은 읽기를 버린다(한 줄). 취소의 결과가 마지막이다.
- 시험(`tests/test_local_jobs_675.py` 1건): 상태 읽기를 이벤트로 막아 둔 채 `task.jobs_cancel`로 job을 끝내고 읽기를 풀면, job은 `cancelled`로 남고 `job.state`는 `cancelled` 하나뿐이며 `jobs.finished`가 나가지 않는다. main의 코드에서는 `completed`로 덮여 실패한다. 파일 전체 37 passed, 1 skipped.
- 리뷰 P1로 넓힌 것: 취소의 `kill`이 실패하면 `_end_local_job`은 job을 `terminal`로 두지 않고 `cancel_requested`만 설정해 다음 폴링이 다시 끝내게 한다. 처음 가드(`terminal`만)는 이 경우를 통과시켜, 낡은 `completed` 읽기가 job을 끝난 것으로 만들고 살아 있는 프로세스 트리를 놓칠 수 있었다. 가드를 `terminal` 또는 `cancel_requested`로 넓혔다(다음 폴링 맨 앞의 기존 분기가 재시도한다). 시험 1건(kill이 실패한 취소 뒤 낡은 읽기가 job을 끝내지 못하고, 다음 폴링이 `cancelled`로 끝내 프로세스가 남지 않음)을 더했고 이전 가드에서는 실패한다.
- 같은 부류의 세 번째 변형(리뷰 P1)과 구조적 수정: 처음 두 가드는 취소가 상태를 기록한 뒤(`terminal`·`cancel_requested`)만 봤다. 취소가 시작됐지만 `kill`이 아직 반환하지 않은 동안에도 낡은 읽기가 job을 끝낼 수 있었고, 뒤늦게 kill이 실패하면 살아 있는 프로세스 트리를 영구히 놓칠 수 있었다. 사례를 하나씩 막지 않고 부류를 닫았다: `_end_local_job`이 첫 줄에서 job을 "끝내는 중" 집합(`_ending_local_jobs`)에 넣고 마지막 줄에서 빼며, 폴링은 읽기 전후에 이 집합과 `terminal`·`cancel_requested`를 모두 본다. 시험 2건(kill이 반환되기 전 낡은 읽기가 돌아옴: kill 성공·실패)을 더했고 이전 가드에서는 둘 다 실패한다.
- 미확인: HPC job 폴링(`_poll_jobs`)에 같은 패턴이 있는지는 이번 범위가 아니라 보지 않았다(HPC job은 이 취소 경로를 쓰지 않는다). 창이 좁아 실제 발생 빈도는 재지 못했다.
- 근거: #1139 판정 댓글 6535399863, #1144.
