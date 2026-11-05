# #1125 rev7 runner `_ack_now` commit 합치기

- 결론: ack commit이 한 요청에 81회였던 이유는, gateway가 메시지마다 ack를 보내고 각 ack가 앞 commit이 끝난 뒤 도착해 겹치는 일이 없었기 때문이다(#1135의 합치기는 commit 중 도착한 ack만 합친다). `_ack_flush`가 직전 commit 뒤 `ACK_MERGE_S`(0.5초)가 지나기 전에는 다음 commit을 미루고, 그 사이 ack는 누적값 하나로 합친다. 한가한 runner의 첫 ack는 그대로 즉시 commit한다.
- 지킨 것: `_ack_drain`(종료·재연결·대기 호출)이 대기 신호를 세워 0.5초를 기다리지 않고 바로 commit한다. 늦은 delete는 크래시 시 몇 행의 재전송이고 gateway `runner_cursor`가 거른다. outbox 행은 ack 전까지 남으므로 #606 보장은 같다.
- 측정(D:, `synchronous=FULL`, lab 요청 1건, 같은 세션 2회씩): commit 542 → 493~495(실제 COMMIT 538 → 489~491), `_ack_now` 81 → 32~34(합 3.6~4.3 s → 1.3~1.4 s), 요청 wall 19.3~20.1 s → 18.8 s. wall은 runner 쪽 commit이 gateway 경로와 병렬이라 commit 수만큼 줄지 않는다.
- 시험: `tests/test_state.py` 신규 2건(간격이 벌어진 ack 8개가 3회 이하로 합쳐짐, drain은 창을 기다리지 않음), 기존 ack·종료·durable·loop-liveness 62건 통과. 신규 두 건은 수정 전 `ACK_MERGE_S`가 없어 실패한다.
- 바꾼 것: `labhq/runner/daemon.py`, `tests/test_state.py`.
- 미검증: Windows CI 전체, 실제 ws 재연결 중 크래시 재전송 양.
