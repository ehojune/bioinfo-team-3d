## 단계
#1129(#1125 A안): `publish`의 이벤트 append와 request 저장을 한 transaction으로.

## 한 일
- `labhq/store.py`: `append_event`/`aappend_event`/`_append_event_now`에 `request=(rid, snapshot)`을 더했다. 같은 transaction에서 request를 먼저 쓰고 이벤트와 runner cursor를 쓴다.
- `labhq/gateway/server.py`: `publish`가 `_append_event_with_request`로 이벤트와 request를 한 번에 저장한다. 기존 request 저장의 순서 규칙(request lock, `_request_changed`, `apply_changes`)은 그대로다. 별도 `asave_request` 호출은 없앴다.
- 보장: 이전에는 이벤트 commit 뒤 request 저장이 실패하면 이벤트만 남았다. 이제 둘 다 되거나 둘 다 안 된다.
- 시험(`tests/test_gateway_publish_durable.py`): "두 commit"을 가정한 매개변수(`commit=2`) 둘을 없애고(둘째 commit이 없다), 새 보장을 단언하는 시험을 더했다(한 publish는 한 commit, commit 실패 시 이벤트·request·runner cursor 모두 그대로). `request_snapshot_does_not_replace_a_later_save`는 첫 commit을 멈추는 방식으로 바꾸고 live request가 되돌려지지 않음을 더 단언한다. 지운 단언은 없다.

## 실측 (D: 드라이브, lab 요청 1회, 같은 스크립트로 main과 이 브랜치 각 1회)
| | 실제 transaction(commit) | commit 시간 합 | 요청 시간 |
|---|---|---|---|
| main | 844 | 32.9 s | 29.4 s |
| 이 브랜치 | 670 | 23.9 s | 24.0 s |
- commit −174(−21%), 요청 −5.4 s(−18%). 1회씩이라 요청 시간의 오차는 확인하지 못했다. 스크립트: `D:/.labhq/dev/tmp/claude-worker3/measure1129.py`.

## 확인
- `tests/test_gateway_publish_durable.py`, `test_state.py`, `test_approval_durable_606.py`, `test_durable_writer.py`, `test_state_control_async_606.py`, `test_step_state_async_606.py`, `test_request_lifecycle.py` 293 passed.

## 하지 않은 것
- 전체 suite와 Windows CI는 돌리지 않았다(CI에 맡김). #1125의 C안(runner 배치)은 W2의 별도 PR이라 이 측정에 들어 있지 않다.
- E안(task activity 등을 이 transaction에 싣기)은 이번 범위가 아니다.

## 리뷰·CI 후 수정 (rev2)
- P1 롤백: joint commit 실패 시 request revision이 그대로면 live request를 저장본으로 되돌리거나 지운다.
- P1 manual: 한·영에 발행의 한 transaction 보장과 실패 시 복원을 적었다.
- CI 실패 두 건의 원인은 commit이 줄어 이벤트 루프 턴 수가 달라진 것이다. `test_quota_wait`는 시험이 단계 A의 대기 진입 순서에 기대고 있었고, `test_pi_language`는 제품의 경합(예산 확인과 reservation 저장 사이에 await가 있어 번역 두 건이 모두 확인을 통과)이 이전 timing에 가려져 있었다. 후자는 `pi_language.py`에서 request별 lock으로 고쳤다(실제 서비스에서도 예산을 넘겨 예약할 수 있었다). `test_pi_language.py`는 그대로다.
- 관련 시험 438 passed, 1 skipped.
