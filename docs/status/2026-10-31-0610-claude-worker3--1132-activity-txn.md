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
