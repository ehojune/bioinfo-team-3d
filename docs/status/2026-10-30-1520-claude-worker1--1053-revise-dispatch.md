# #1053 CP2 revise 이어 가기 'step ended before dispatch'

- 결론: 재현했고 고쳤다. 이어 가기 라운드는 앞 라운드에서 끝난 단계의 같은 id를 revision 0으로 다시 dispatch했다. 게이트웨이는 끝난 단계(`_closed_steps`)를 그 단계 turn의 최대 revision에 묶어 두고, 다시 열려면 그보다 높은 revision이 필요하다(`_update_step(reopen=...)`). 리뷰 revise는 `pending_revisions`로 revision을 올리지만 연구 이어 가기는 올리지 않아 `gateway/server.py`의 닫힌 단계 검사가 거절했다(#910 5차 `s1_access_lookup`).
- 바꾼 것: `Hub.closed_step_revision`(끝난 단계의 revision, 열려 있으면 -1)과 `Orchestrator._step_revision`(리뷰 revise의 revision과 닫힌 revision+1 중 큰 값)으로, 다시 열리는 단계만 한 단계 높은 revision으로 dispatch한다. 처음 도는 단계와 리뷰 revise는 값이 그대로다.
- 닫힌 단계 보호: 검사는 그대로 두었다. 앞 라운드의 낮은 revision turn은 다시 열린 뒤에도 `revision < step revision`으로 거절된다(시험으로 고정).
- 시험: `tests/test_resume_cards_607.py`에 3건(닫힌 상태 재현, revision 상향·낡은 turn 거절, 오케스트레이터 revision 계산). 관련 시험 310건 통과.
- 미검증: 실제 연구 요청에서의 CP2 revise → 이어 가기 end-to-end(#910 다음 시도에서 확인). 이어 가기 계획이 재사용을 거부한 이유("request-level input hash record missing")는 이 PR이 다루지 않는다.
