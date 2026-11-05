## 단계
#1132(#1125 E1): task activity를 이벤트 transaction에.

## 한 일
- `labhq/store.py`: `aappend_event`/`append_event`/`_append_event_now`에 `activity=(key, reducer)`를 더했다. reducer는 writer thread에서 같은 transaction 안의 최신 task 행에 적용된다(`_apply_activity`로 분리).
- `labhq/gateway/server.py`: `on_runner_message`가 activity를 따로 commit하지 않고 마지막 `publish(..., activity=...)`에 실어 보낸다. 이벤트, request 저장본, activity, runner cursor가 한 transaction이다.
- 시험: `test_state_activity_durable.py`의 publish stub을 실제 publish로 바꿨다. 단언은 약하게 하지 않았고 의미가 바뀐 한 곳만 새 시험으로 명시했다. 취소가 commit 중이면 이전에는 activity만 남고 이벤트·cursor는 없었다. 이제 commit이 끝나면 이벤트·activity·cursor가 함께 남고 재전송은 중복으로 걸러진다(ack만 보냄). commit이 실패하면 모두 없고 재전송은 다시 적용된다. `test_gateway_publish_durable.py`에 세 시험을 더했다(한 publish는 한 commit·실패 시 모두 없음, `agent.tool` 40건 폭주에서 메시지당 한 commit·순서·전 tool 보존, 느린 client가 락을 잡아도 timeout 안에 activity 저장).
- manual 한영의 발행 보장 문장에 activity를 더했다.

## 실측 (D: 드라이브, lab 요청 1회, main과 이 브랜치 각 2회)
| | transaction | commit 시간 합 | 요청 시간 |
|---|---|---|---|
| main | 635, 641 | 0.7 s, 0.5 s | 16.2 s, 15.8 s |
| 이 브랜치 | 587, 586 | 0.6 s, 0.6 s | 14.1 s, 16.3 s |
- transaction −48~−55(약 −8%). lab은 #1126 이후 `synchronous=NORMAL`이라 commit 시간 합이 0.5~0.7 s로 줄어 fsync 비용이 보이지 않고, 요청 시간 차이(−2.1 s, +0.5 s)는 오차 안이다. 기본 `synchronous=FULL`에서 건당 약 37 ms로 계산하면 −48건은 약 −1.8 s로 **추정**한다(측정 아님).

## 확인
- 관련 시험 13개 파일 503 passed, 1 skipped.

## 하지 않은 것
- 기본 FULL 모드의 요청 시간은 측정하지 않았다(lab이 NORMAL).
- 전체 suite와 Windows CI는 CI에 맡긴다.

## CI 후 수정 (rev6)
- `test_state_control_durable::test_halt_after_activity_read_keeps_committed_tool`이 3.10·3.12·Windows에서 실패했다. 원인은 시험이 `hub.publish`를 빈 `AsyncMock`으로 바꿔 activity가 publish 안으로 옮겨진 뒤 저장되지 않은 것이다. 제품 순서는 안전하다(halt는 `aupdate_task_control`, activity는 이벤트 transaction의 reducer로 같은 writer lane에서 최신 행에 병합).
- stub이 `store.aappend_event(..., activity=activity)`를 실제로 부르게 했고 단언은 그대로다. 같은 부류를 찾으려고 publish를 stub한 시험 14개 파일을 훑었고 activity에 기대는 파일은 `test_state_activity_durable`(이미 수정)과 이 파일뿐이었다.
- 관련 4개 파일 72 passed.

## main 반영 (rev8)
- #1151 병합 뒤 `server.py`·`store.py`·`test_gateway_publish_durable.py`의 충돌을 풀었다. #1143의 중복 runner 메시지 건너뛰기는 cursor 검사에서 먼저 `None`을 돌려주므로 activity는 중복 복사본에 다시 적용되지 않는다.
- 합친 뒤 `test_state::test_a_resend_waits_for_the_first_copy_...`가 `publish(ev, runner_id, runner_seq)` 시그니처로 감싼 double에 `activity=`가 넘어가 실패했다. `activity` 인자를 activity가 있는 메시지에만 넘기게 해 시험을 고치지 않고 풀었다.
- 관련 시험 174 passed.
