# #1076 lab의 startup 실패가 worker 전체를 끌고 가지 않게

- 결론: PR #1110의 3.10 run에서 `test_the_lab_records_the_same_with_actions_on[shadow]`가 `SystemExit: 3`으로 실패한 뒤 같은 worker(gw2)의 146건이 연쇄로 실패했다. uvicorn은 startup이 실패하면 `sys.exit(3)`을 하고, task 안의 SystemExit는 event loop 밖으로 새어 worker를 무너뜨린다. 어느 startup 훅이 실패했는지는 CI 기록에 남지 않아 확정하지 못했다. 제품 코드는 바꾸지 않았다.
- 바꾼 것: `tests/semantics_shadow_lab.py`의 `run_lab`이 `server.serve()`를 `_serve`로 감싸 `SystemExit`를 uvicorn 로그 꼬리(`uvicorn.error` 마지막 40줄, 트레이스백 포함)가 담긴 `RuntimeError`로 바꾼다. 시작 실패는 `_until` 시간 초과 대신 바로 드러난다.
- 실행한 것: startup에서 예외를 내도록 만든 lab으로 확인했다. 옛 코드는 `SystemExit: 3`으로 프로세스가 끝나고, 새 코드는 exit 3과 uvicorn 트레이스백이 담긴 `RuntimeError`이며 프로세스가 산다. 이 lab을 쓰는 9개 시험 파일 226 passed, 1 skipped.
- 미확인: 같은 구조의 다른 `uvicorn.Server` 사용처(`test_ask.py`·`test_bench*.py`·`test_e2e_mock.py`·`test_followup.py` 등)는 이번 범위가 아니다. 원래 startup 실패의 원인은 다음에 같은 일이 나올 때 이 로그 꼬리로 본다.
- 근거: #1076 댓글 6528583819.
