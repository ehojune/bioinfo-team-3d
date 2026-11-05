# #1076 halt 시험의 실제 프로세스 제거

- 결론: #1189 CI의 3.10 job이 25분 상한에 끊겼고 stall 덤프 로그에서 시작만 하고 끝나지 않은 시험이 둘이었다. 하나가 `tests/test_halt_watch_1174.py::test_a_completed_turn_whose_handler_is_still_awaiting_is_not_halted`였다. 그 시험은 가짜 Gemini CLI 스크립트를 실제 프로세스로 띄워 결과 뒤 4초를 살려 둔다. 이 경로가 정리되지 않을 때 `asyncio.wait_for`로 끊지 못하고 멈출 수 있다. Gemini 경우와 Claude "결과 뒤 halt" 경우를 `FakeProc`(OS 프로세스 없음, 스트림 두 개, 끝나는 시각을 시험이 정함)로 바꾸고 시험 안에 5초 상한(`LIMIT_S`)을 뒀다.
- 확인: 제품 코드를 바꾸지 않았고 두 시험 모두 `halt_watch`의 `st.result_seen` 확인을 빼면 실패한다(이전 변이 확인과 같음). 로컬에서 6회 반복해 모두 통과.
- 남은 것: `test_a_halt_that_finds_the_cli_still_running_still_stops_it`는 실제 프로세스를 죽이는 것이 시험 대상이라 그대로 둔다(상한 30초, 별도 관찰 대상). 같은 job의 다른 미종료 시험 `test_paper_skill_recovery_identity...[cancel]`은 이 claim 밖이다.
- 미검증: 이 변경이 3.10·Windows 정지를 실제로 없애는지(CI 재실행으로 확인). 정지의 원인이 이 시험이었다는 증거는 "시작만 하고 끝나지 않은 시험 둘 중 하나"까지다.
