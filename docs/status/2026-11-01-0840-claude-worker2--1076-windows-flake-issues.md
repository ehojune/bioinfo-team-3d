# #1076 rev27: 열린 Windows CI 간헐 실패 이슈 네 건 점검

- 결론: #1029·#844·#1107은 수정 뒤 재발이 없어 닫았고, #898은 R parse 쪽만 재발이 없고 SIGTERM·SIGHUP ACK 쪽에서 새 시험이 재발해 열어 두었다. 코드는 바꾸지 않았다.

| 이슈 | 수정 | 수정 뒤 재발 | 처리 |
|---|---|---|---|
| #1029 breakaway | #1035(10-29T13:27Z) | 0건(최종 실패 117 + 통과 361 run) | 닫음 |
| #844 supervisor 시작 | #851·#873(10-22·23) | 0건(10-23 이후 W1 80건, 10-28 이후 144+446 run) | 닫음, 원인은 미확정 |
| #1107 web_resizable_panels | #1109(10-30T17:03Z)·#1133(10-30T21:53Z) | 0건(#1133 이후 46+199 run). 다른 부류 `web_office_focus_layout` 30초가 PR #1221에서 한 번(재실행 통과) | 닫음 |
| #898 R parse·SIGTERM ACK | #900(10-23)·#1002(10-28T23:44Z) | R parse 0건. `test_runner_shutdown_signals`가 main에서 3번(10-30 16:31 SIGHUP, 10-30 18:30·10-31 15:57 SIGTERM, Windows) | 열어 둠, 시험 전용 수정안과 claim 요청 |

- #898 가설: 시험의 `gateway_resume` 단계가 느린 Windows 러너에서 5초를 넘고, runner `shutdown(timeout_s=5)`가 그 안에 ACK를 못 받아 일찍 돌려보낸다. 수정안은 시험이 `shutdown(timeout_s=30)`과 단계 한도 30초를 쓰는 것이다(순서 단언은 그대로).
- 근거: 각 이슈 댓글(#1029, #844, #1107, #898). 집계는 `tally1076w.py`·`tally1076w.json`(test.yml 최종 실패 run의 pytest-windows 로그).
- 한계: 재실행해 통과한 job은 run 결론이 success로 덮여 보이지 않는다. #898의 원인은 재현하지 않았다.
