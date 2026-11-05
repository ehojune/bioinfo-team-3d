# test_office_in_node 간헐 실패 (#1272)

| 실패 | 원인 | 조치 |
|---|---|---|
| `web_office_browser.cjs` 포트 단언 (main 981c1c88) | `server.listen(0)`이 예약 포트 55921을 받음(약 1/16,384) | `listenOnUnreservedPort`(`tests/support/demo_driver.cjs`)로 다시 listen. 드라이버의 #1072 처리와 같은 함수 |
| `web_office_focus_layout.cjs` `timed out waiting for demo boot` (PR #538 중간 head) | 원인 미특정. 로컬 13회 재현 실패 | `waitReal` 시간 초과 시 `boot report`(조건별 값·readyState·페이지 예외·정적 서버가 받은 경로·미완료/실패 요청)를 오류에 붙임 |

- 시간 상한과 단언은 바꾸지 않았다. 재발하면 `boot report`로 #1272에서 원인을 좁힌다.
- `tests/web_visual_tooling.cjs`의 `listen(0)`은 포트 값을 단언하지 않는 소켓 시험이라 손대지 않았다.
- 증거: [#1272 진단](https://github.com/ehojune/bioinfo-team-3d/issues/1272#issuecomment-6649683159)
