## #842 — PR843 첫 CI 실패 분리 (source c7a18c6, 첫 CI는 18abfff run 39684337037)

- 수정(3플랫폼 공통): `web_visual_tooling.cjs` startup fake 5·6·7은 내 예산 배분 회귀였다. Chrome이 없는 부분 시작에서도 3초 phase timer를 만들어, 5초 deadline 하나만 허용하는 VM sandbox의 `setTimeout` 단언이 cleanup 안에서 실패했다. 지금은 소유 Chrome을 획득했을 때만 그 timer를 만든다.
- 미해결(Windows): 새 실Chrome forced test 42가 실패했다(5.6초, `demo cleanup incomplete`).
  - 기록: forced(3초) → timeout(5초) → `late-chrome-stdio-close` `after_deadline_ms` 13030. `late-chrome-exit` 행은 없다. 따라서 소유 launcher의 exit는 마감 전에 관측됐고, 그 뒤 stdio pipe가 약 13초 더 열려 있었다(#832와 같은 형태).
  - 정직한 완료 규칙(실제 stdio close 필요)으로는 Windows forced 경로가 5초 안에 끝날 수 없다. 규칙 결정이 필요하다(lookup842-ci-fix0728.md).
- 별도: Windows `test_supervisor_process.py::test_a_real_gateway_runs_as_its_supervisors_child_and_down_ends_both`가 `SystemExit: 2`로 실패했다. 이 PR 범위 밖이라 보고만 한다.
- 검사: bundled Node `--check` 통과, 정적 CI 계약 199 passed, profile·public·diff 검사 통과. 실제 JS·Chrome·CI는 미실행이다.
- 새 head에 전체 CI가 다시 필요하다. 18abfff의 결과와 리뷰는 새 head의 성공 근거로 쓰지 않는다.
