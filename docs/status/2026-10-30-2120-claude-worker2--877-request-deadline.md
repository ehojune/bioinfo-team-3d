# #877 요청 전체 벽시계 상한 (gateway 집행)

- 결론: ② 6차를 대조한 결과(#877 댓글) 평범한 `labhq up`에는 요청 전체 상한이 없었다. gateway가 요청 단위 상한을 고정하고 집행하도록 했다. 설정은 기본으로 꺼져 있다.
- 바꾼 것: `orchestrator.request_wall_clock_s`(0이면 끔, 60초 미만은 거절). 요청을 받을 때 `wall_clock`(limit_s, held_s, deadline_at)을 요청 기록에 고정해 이후 설정 변경이 영향을 주지 않는다. gateway 감시 루프(15초)가 활성 요청이 `created_at + limit + held_s`를 넘기면 기존 `cancel_request(active_only=True)`로 취소하고 `cancel_reason=request_deadline`과 `deadline_receipt`를 남긴다.
- 시계 규칙(총괄 결정): 요청 수락부터 `review_pending` 진입까지를 센다. 이 구간의 도구 카드 대기는 포함한다. `waiting_pi`에 머문 시간은 `held_s`로 기한에 더하고, `review_pending` 이후와 `interrupted` 요청은 끊지 않는다. 재시작은 같은 `created_at`과 기록된 `held_s`를 유지하고 꺼져 있던 시간은 빼지 않는다.
- 영수증: 취소한 task, 취소 뒤 결과를 보고하지 않은 task, 종료 판정(`confirmed`·`unconfirmed`·`no_running_tasks`). runner에 새 ack를 만들지 않고 기존 `_await_cancelled_results`가 결과를 못 받은 task를 "미확인"으로 적는 경로를 썼다.
- 시험: `tests/test_active_request_cancel.py`에 8건(기한 취소·영수증·라벨, 설정이 아니라 요청 기록의 한도를 읽음, `waiting_pi` 가산, 검토 이후·중단 요청 비적용, 재시작 유지, 미확인 종료, 배경 감시 시작·정지, 설정 검증). 기존 취소 시험 포함 관련 시험 통과.
- 미확인: 실제 모델 호출 중인 직원과 자식 프로세스를 기한에 끊는 동작(단위 시험은 task 상태만 본다, 별도 disposable instance 확인 필요), Windows Job 경계. 상한이 켜진 실제 요청은 아직 돌려 보지 않았다. 감시는 15초 간격이라 기한 판정에 최대 그만큼의 오차가 있다.
- 근거: #877 댓글(6차 대조와 결정), `labhq/gateway/server.py`의 `enforce_request_deadlines`.
