# #1076 종료 ack 시험의 shutdown 한도 5초

- 결론: `test_shutdown_commits_the_acks_it_already_received`의 `shutdown(timeout_s=...)`를 0.05초(#1181 전) → 0.5초(#1181) → 5.0초(제품 기본값)로 올렸다. 단언(이미 받은 ack를 종료 전에 commit한다)과 제품 코드는 그대로다.
- 관측: 0.05초와 0.5초 모두 Windows 러너에서 `[1,2,3,4,5] == [5]`로 실패했다(#1180 attempt 2, #1182 attempt 1). 두 번 다 로그에 `runner local: an ack commit is still running; its rows stay in the outbox`가 있어, ack commit 하나가 shutdown의 drain 한도 안에 끝나지 않았다. 재실행에서는 통과했다.
- 원인: **첫 commit이 한 번 느렸던 것으로 추정하며 재지 않았다.** 이 시험은 task가 없어 `finish()`가 마감을 먼저 쓰는 경로가 아니다(`finish()`가 바로 반환하고 drain이 거의 전체 한도를 받는다). `_ack_flush`의 대기(`ACK_MERGE_S`)는 drain이 `hurry`를 세워 건너뛰므로 원인이 아니라고 코드로 확인했으나 실행으로 확인하지는 않았다.
- 시험: `-k shutdown` 3건 통과.
- 미검증: Windows CI에서의 안정성(이 PR이 첫 확인), 첫 commit 지연의 실제 시간. 다시 실패하면 `_ack_flush` 첫 commit 시간을 러너에서 재는 진단이 필요하다.
