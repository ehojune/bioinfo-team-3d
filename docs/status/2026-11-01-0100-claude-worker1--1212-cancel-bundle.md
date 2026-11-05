# #1212 — 정상 취소의 묶음 경고

- 결론: 분석 단계 작업 폴더가 한 번도 보고되지 않은 채 취소된 요청만 묶음 경고를 내지 않는다. 이 정의 한 줄이 `Hub._bundle_outcome` 앞 주석에 있다. 폴더가 보고됐는데 사라졌거나, 취소가 아닌 종료이거나, 다른 묶음 실패면 경고를 그대로 둔다. 계획·briefing·review task의 폴더는 묶지 않는 폴더라 판정에서 뺀다.
- 구조: 취소된 run도 만든 작업 폴더를 결과에 싣는다(`Runner._run_guarded`의 취소 경로가 `workdir`·`workdir_id`를 채움). 그래서 게이트웨이는 "폴더가 있었는데 기록이 없다"와 "폴더가 없었다"를 구분한다.
- 바꾼 것: `labhq/request_bundle.py`(`NoStepWorkdirs`), `labhq/gateway/server.py`, `labhq/runner/daemon.py`, `tests/test_request_bundle.py`, manual 한·영(예외 한 문장).
- 시험(`tests/test_request_bundle.py`, 추가 7건): 취소 시 경고 없음(실제 build) · 실패 시 경고 유지 · 취소지만 다른 OSError면 경고 유지 · 기록된 폴더가 사라진 취소는 경고 유지 · task 결과에만 기록된 폴더도 경고 유지 · 계획 단계까지 마치고 취소해도 경고 없음 · 취소된 run이 만든 폴더를 보고함. 판정 조건을 지우면 해당 시험이 실패하는 것을 확인했다.
- 실행: 관련 시험 1004건 통과(`bundle_warning`·`commit_terminal`·`_run_guarded`를 쓰는 시험 파일 전체).
- 미검증: 요청 단위 e2e 취소 흐름. 수락됐지만 결과가 아예 오지 않은 task(runner가 사라진 경우)는 폴더가 보고되지 않아 조용히 넘어간다.
