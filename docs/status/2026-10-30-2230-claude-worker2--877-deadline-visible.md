# #877 `labhq status`의 시간 상한 표시

- 결론: 사용자가 진행 중인 요청의 시간 상한을 `labhq status`에서 볼 수 있다.
- 바꾼 것: `labhq/request_status.py`의 `request_deadline_text`가 `꺼짐` 또는 `켜짐 N분 · 남은 N분 · PI 대기 제외 N분`을 만들고, `labhq/cli.py`의 status가 요청마다 `시간 상한: …` 한 줄을 요청 기록(`/api/requests/{id}`)에서 읽어 보인다. `waiting_pi` 중에는 시간이 가지 않는다는 말을 덧붙인다. 상한이 없던 때 받은 요청은 `꺼짐`이다. 기록을 못 읽으면 `꺼짐`이라 하지 않고 아무것도 보이지 않는다.
- 문서: `docs/reference/request-deadline.md`·`.en.md`에 표시와 "영수증이 취소 뒤 2~10초 늦게 붙는다"를 적었다.
- 시험: `tests/test_active_request_cancel.py`에 6건(꺼짐, 남은 분, 제외한 PI 대기, PI 대기 중 정지한 시계, 기한 경과, status 줄·읽기 실패). 파일 전체 25건과 `test_request_status.py`·`test_cli_launch.py` 통과.
- 실제 확인: 가짜 엔진 gateway에 한도 5400초로 요청을 보내 `labhq status`를 직접 돌렸다. 진행 중 `시간 상한: 켜짐 90분 · 남은 90분`, 카드 대기 중 `… · PI 대기 제외 13초 · PI 결정 대기 중이라 시간이 가지 않습니다`, 답한 뒤 `… · PI 대기 제외 14초`로 나왔다.
- 미확인: 웹 작업판 표시는 이번 범위가 아니다(요청 목록 요약 API에 `wall_clock`이 없어 필요하면 `labhq/gateway/server.py` 변경이 따로 필요하다).
- 근거: #877 rev6 댓글(영수증 지연 판단), `labhq/request_status.py`.
