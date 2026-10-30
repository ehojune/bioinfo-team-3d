# 요청 전체 시간 상한 (#877)

조회용 문서입니다. 동작은 `labhq/gateway/server.py`의 `enforce_request_deadlines`와 `_count_pi_wait`에 있습니다.

`orchestrator.request_wall_clock_s`(초)를 켜면 gateway가 요청 하나의 전체 벽시계 시간을 직접 셉니다. 단계별 `runner.task_timeout_s`와는 별개이며, 직원이 몇 단계를 거치든 합계가 상한을 넘지 못하게 합니다.

| 항목 | 내용 |
|---|---|
| 설정 | 0이면 끔(기본). 켜려면 60 이상, 최대 7일(604800). 60 미만은 분을 초로 잘못 쓴 것으로 보고 거절합니다. |
| 고정 시점 | 요청을 받을 때 요청 기록의 `wall_clock`에 `limit_s`·`held_s`·`deadline_at`을 적습니다. 나중에 설정을 바꿔도 이미 받은 요청의 한도는 그대로입니다. 끈 요청도 `limit_s: 0`으로 남습니다. |
| 세는 구간 | 요청 수락부터 `review_pending` 진입까지입니다. 이 구간의 도구 카드 대기는 직원이 살아 있으므로 포함합니다. |
| 빼는 구간 | `waiting_pi`(PI 결정 카드 대기)에 머문 시간은 `held_s`로 기한에 더합니다. 상태 전환 시각으로 정산하므로 감시 간격(15초)과 무관합니다. `review_pending` 이후의 검토·게시와 `interrupted` 요청은 끊지 않습니다. |
| 취소 | 기한(`created_at + limit_s + held_s`)이 지난 활성 요청을 기존 `cancel_request`로 취소하고 `cancel_reason: request_deadline`을 남깁니다. 표시는 "시간 상한으로 취소됨", 보고서는 "요청 전체 시간 상한에 닿아 labhq가 취소했습니다"로 적힙니다. |
| 영수증 | `deadline_receipt`에 `limit_s`·`held_s`·`deadline_at`·`fired_at`·취소한 task·결과를 보고하지 않은 task·종료 판정이 들어갑니다. |

## 종료 판정
- `confirmed`: 취소한 task가 모두 취소 뒤 결과를 보고했습니다. runner는 프로세스 트리를 끝낸 뒤 결과를 보고합니다.
- `unconfirmed`: 결과를 보고하지 않은 task가 있습니다. 멈췄다고 가정하지 않고 task 목록을 적습니다.
- `no_running_tasks`: 취소할 task가 없었습니다.

## 재시작
- 같은 `created_at`과 기록된 `held_s`를 유지합니다. gateway가 꺼져 있던 시간은 빼지 않습니다.
- 재시작은 활성 요청을 `interrupted`로 바꿉니다. 이어 가면 같은 기한이 적용되고, 기한이 이미 지났으면 바로 취소합니다.
- 재시작이 열어 둔 `waiting_pi` 구간은 더하지 않습니다.
- 취소 도중 gateway가 꺼져도 다음 시작의 `finish_interrupted_cancels`가 취소를 끝내며 영수증을 만듭니다.

## 한계와 미확인
- 감시는 15초 간격이라 기한 판정에 최대 그만큼의 오차가 있습니다.
- 실제 모델 호출 중인 직원과 자식 프로세스를 기한에 끊는 동작은 단위 시험(task 상태)으로만 확인했습니다. 별도 격리 instance 확인이 필요합니다.
- 외부 Windows Job 소유자(proof c2) 경로는 이 설정과 별개의 바깥 상한입니다. 이 설정이 대신하지 않습니다.
- 상한을 켠 실제 요청은 아직 돌려 보지 않았습니다.

검증: `tests/test_active_request_cancel.py`
