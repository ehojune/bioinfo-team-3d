# #1076 환경·부하에 흔들리던 시험 세 건

- 결론: 세 시험 모두 시험 쪽 문제였고 제품 코드는 바꾸지 않았다.
- 바꾼 것: `tests/test_doctor.py`는 시작에서 `CLAUDE_*`·`CLAUDECODE*` 변수를 지우고 시험 마커 2개만 센다. `tests/test_staff_control_steps.py`의 `_alive()`는 `tasklist` 출력을 bytes로 읽는다(한국어 Windows에서 `PYTHONUTF8=1`이면 해독 실패). `tests/test_policy.py`의 `_cost`는 평균 대신 가장 빠른 호출을 쓰고 한 쪽당 최소 3회 잰다.
- 검출력: 가짜 시계 시험 4건(느린 호출 1회는 통과, 모든 호출이 느리면 실패)과 실제 CPU로 제곱 비용을 내는 시험 1건을 더했다. 실제 제곱 시험은 `must stay linear`로 거부된다.
- 실행한 것: 에이전트 세션 환경(`CLAUDE_*` 10개, `PYTHONUTF8=1`)에서 세 파일 345 passed. Linux 3.12 러너 지연은 재현하지 못했다.
- 근거: #1076 댓글 6525988210.
