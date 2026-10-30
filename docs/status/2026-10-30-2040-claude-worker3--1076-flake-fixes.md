## 단계
#1076(CI 간헐 실패 정리) 수정 1단계: 확정된 ④와 계측만 하는 ②. 집계·근거: [#1076 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6520390705).

## 한 일
1. **④ `scripts/repeat_fake_fixture.py`:** `port()`가 OS가 준 포트가 `RESERVED_PORTS`(8787·55921)이거나 이미 쓴 gateway 포트면 최대 `PORT_DRAWS`(8)번 다시 뽑는다. 8번 모두 예약 포트면 지금처럼 `reserved port`로 거절한다(#1072와 같은 방식). 시험 2건을 등록된 `tests/test_repeat_fake_check.py`에 더했고 수정 전 코드에서 실패함을 확인했다.
2. **② `tests/test_state.py`:** `test_completed_direct_result_finishes_without_original_agent`의 단언이 실패하면 요청 상태·오류·최근 이벤트 12개·승인 목록·요청 키를 메시지에 담는다. 폴링 창(100×10 ms)은 늘리지 않았다. 강제로 실패시켜 메시지 형식을 확인했다.

## 테스트 결과
- Windows `test_repeat_fake_check`·`test_recovery_fake_check`·`test_state` 해당 시험 통과(33 passed + 2 passed).
- 미검증: ②는 계측만이라 원인은 다음 실패 때 확인해야 한다. 로컬에서 40회 돌려도 재현하지 못했다. ①(9분 상한)·③(recovery ReadTimeout)은 이 PR 범위 밖이다.
