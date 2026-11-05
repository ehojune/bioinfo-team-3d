# #1076 종료 ack 시험의 shutdown 한도

- 결론: `tests/test_state.py::test_shutdown_commits_the_acks_it_already_received`가 `shutdown(timeout_s=0.05)` 안에 ack commit(fsync 포함)이 끝나야 해서, Windows 러너에서 commit이 36~67 ms 걸리면 한도에 닿아 `[1,2,3,4,5] == [5]`로 실패했다(#1180 attempt 2). 한도를 0.5 s로 올렸다. 단언(이미 받은 ack를 종료 전에 commit한다)은 그대로다. 제품 코드는 바꾸지 않았다.
- 같은 부류로 확인한 것: `test_shutdown_stays_inside_its_deadline_when_an_ack_commit_hangs`는 0.2 s 한도와 `<1.5 s`로 상한을 보는 시험이라 더 느린 commit에 영향이 없다.
- 시험: `-k shutdown` 통과.
- 미검증: Windows CI에서의 안정성(이 PR의 CI가 첫 확인).
