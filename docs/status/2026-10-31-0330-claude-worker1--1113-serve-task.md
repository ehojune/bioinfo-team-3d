# #1113 uvicorn 시작 실패가 worker를 죽이지 않게

- 결론: 새 helper `tests/support/serve_task.py`(`serve_task`·`wait_started`)가 `server.serve()`·`broker.serve()`의 `SystemExit`를 uvicorn 로그 꼬리가 담긴 `RuntimeError`로 바꾼다. 시작이 실패하면 그 시험만 원인과 함께 실패하고, 시간 초과까지 기다리지 않는다.
- 바꾼 곳: 시험 10개 파일의 서버 시작 12곳(`test_ask`는 gateway 1·broker 2). 줄 수 변화는 호출 줄뿐이고 단언은 그대로다. 기다림 한도는 원래 값을 유지했다(`test_web_3d`는 2초에서 10초로 늘렸다).
- 새 시험: `tests/test_serve_task.py` 5건(점유된 포트, 정상 시작·종료, 로그 없는 종료, 시간 초과, handler 정리).
- inventory: `known_tests`에 `tests/test_serve_task.py` 한 줄, `mixed_nodes["tests/test_web_3d.py"].sha256`만 새 해시. `validate_profile()` 통과.
- 확인: 바꾼 시험 파일 213건과 `test_web_3d` 서버 시험 2건, `test_ci_boundary_preview` 통과.
- 제외: `tests/semantics_shadow_lab.py`는 #1111 범위라 병합 뒤 이 helper로 모은다.
- 미검증: Windows CI. 포트 경합이 실제 CI 연쇄의 원인인지.
