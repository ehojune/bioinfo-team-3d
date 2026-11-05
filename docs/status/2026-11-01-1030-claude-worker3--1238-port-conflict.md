# #1238 rev4 — 포트 충돌이 worker 전체를 끝내던 문제

| 항목 | 내용 |
|---|---|
| 결론 | `Runner.run_forever`가 broker 시작 실패의 `SystemExit`를 `RuntimeError`로 바꾼다. 시험의 포트 두 번 뽑기는 `free_ports`로 바꿨다 |
| 바뀐 것 | `labhq/runner/daemon.py`, `labhq/util.py`(`free_ports`), `labhq/bench.py`·`labhq/cli.py` demo(포트 두 번 뽑기를 `free_ports`로), `tests/semantics_shadow_lab.py`, 이를 가로채던 `tests/test_bench_metrics.py`·`tests/test_bench_receipt_review.py`, 회귀 시험 2건(`test_durable_writer.py`, `test_cli_launch.py`), manual 한·영 |
| 실행한 것 | 새 시험 2건 통과, 옛 daemon 코드로 되돌리면 runner 시험이 `SystemExit: 3`으로 실패, 관련 시험 172건 통과 |
| 미해결 | `labhq runner`를 포트 충돌로 시작하면 종료 코드가 uvicorn의 3에서 예외 종료로 바뀐다(메시지는 포트를 알려 준다. manual에 적음). 3.10 CI에서 포트 충돌이 다시 날 때의 모습은 확인하지 못했다 |
| 근거 | https://github.com/ehojune/bioinfo-team-3d/issues/1238 |
